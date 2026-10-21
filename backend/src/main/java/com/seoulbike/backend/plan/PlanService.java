package com.seoulbike.backend.plan;

import java.util.Optional;
import java.util.UUID;
import java.util.stream.Collectors;

import org.springframework.stereotype.Service;
import org.springframework.transaction.support.TransactionTemplate;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ArrayNode;
import tools.jackson.databind.node.ObjectNode;

import com.seoulbike.backend.audit.AuditLog;
import com.seoulbike.backend.common.ApiException;
import com.seoulbike.backend.plan.PlanRepository.Plan;
import com.seoulbike.backend.route.RouteChangeGrade;
import com.seoulbike.backend.task.ChangeApprovals;

/**
 * 계획 조회와 두 변경 API (계획 활성화, 트럭 경로 적용). 계약: internal-api, openapi Plan·Route.
 *
 * <p>변경은 승인 하나에 한 번이다. 실행한 승인은 plans.activated_by_approval(V8)에 남고, 같은 승인으로 다시 부르면
 * 처음 결과를 돌려준다 (agent 재시도). 다른 변경에 쓰려 하면 409 APPROVAL_ALREADY_USED.
 *
 * <p>버전: 활성화는 후보의 version을 그대로 ACTIVE로 만든다. 경로 적용은 활성 계획을 복사해 그 트럭 경로만 후보의 것으로
 * 바꾼 새 version(max+1)을 ACTIVE로 넣는다. 어느 쪽이든 이전 ACTIVE는 CLOSED가 되고, 근무조 잠금 안에서 한 트랜잭션이다.
 * 후보는 계산을 요청할 때의 활성 version(solve_requests.plan_version)을 기준으로 만들어졌으므로, 그 사이 활성 계획이
 * 바뀌었으면 409로 막는다. 막지 않으면 다른 변경을 모르고 계산한 경로가 그 변경을 덮어쓴다.
 */
@Service
class PlanService {

	private final PlanRepository plans;
	private final PlanCandidates candidates;
	private final ChangeApprovals approvals;
	private final AuditLog audit;
	private final TransactionTemplate tx;
	private final JsonMapper json;

	PlanService(PlanRepository plans, PlanCandidates candidates, ChangeApprovals approvals, AuditLog audit,
			TransactionTemplate tx, JsonMapper json) {
		this.plans = plans;
		this.candidates = candidates;
		this.approvals = approvals;
		this.audit = audit;
		this.tx = tx;
		this.json = json;
	}

	/** business-mcp get_current_plan: 근무조의 ACTIVE 계획. */
	ObjectNode current(String shiftId) {
		return plans.activePlan(shiftId)
			.map(this::toContract)
			.orElseThrow(() -> ApiException.notFound("SHIFT_NOT_FOUND", "근무조 " + shiftId
					+ "에 활성 계획이 없다. request_route_plan으로 계획을 만들고 submit_plan으로 관리자 승인을 받아 활성화한다."));
	}

	/** 화면 계획 검토·모니터링: 상태와 상관없이 가장 최근 버전. 승인을 기다리는 후보도 보여야 한다. */
	ObjectNode latest(String shiftId) {
		return plans.latest(shiftId)
			.map(this::toContract)
			.orElseThrow(() -> ApiException.notFound("NOT_FOUND", "근무조 " + shiftId + "에 계획이 없다"));
	}

	JsonNode driverRoute(String truckId) {
		return plans.activeRouteOf(truckId)
			.map(json::readTree)
			.orElseThrow(() -> ApiException.notFound("NOT_FOUND", "오늘 경로가 없다"));
	}

	/** submit_plan. 승인한 관리자를 활성화한 사람으로 남긴다. */
	ObjectNode activate(String planId, UUID approvalId, UUID taskId) {
		String shiftId = plans.find(planId)
			.map(Plan::shiftId)
			.orElseThrow(() -> ApiException.notFound("PLAN_NOT_FOUND",
					"계획 " + planId + "가 없다. request_route_plan 결과의 plan.planId를 쓴다."));
		Plan activated = tx.execute(s -> {
			plans.lockShift(shiftId);
			Optional<Plan> used = plans.activatedBy(approvalId);
			if (used.isPresent() && !(used.get().planId().equals(planId) && used.get().appliedTruckId() == null)) {
				throw alreadyUsed(approvalId, used.get());
			}
			String approvedBy = approvals.requirePlanApproval(taskId, approvalId, planId, shiftId);
			if (used.isPresent()) {
				return used.get(); // 재시도: 감사 기록을 더하지 않는다
			}
			Plan plan = plans.find(planId).orElseThrow();
			switch (plan.status()) {
				case "DRAFT", "PROPOSED" -> {
				}
				case "ACTIVE" -> throw ApiException.conflict("PLAN_ALREADY_ACTIVE",
						"계획 " + planId + "(v" + plan.version() + ")는 이미 다른 승인으로 활성화됐다. get_current_plan으로 확인한다.");
				default -> throw ApiException.conflict("PLAN_SUPERSEDED", "계획 " + planId + "(v" + plan.version() + ")는 "
						+ plan.status() + " 상태라 활성화할 수 없다. request_route_plan으로 새 후보를 받는다.");
			}
			int activeVersion = plans.active(shiftId).map(PlanRepository.Active::version).orElse(0);
			if (plan.requestId() != null) {
				int basedOn = plans.basedOnVersion(plan.requestId()).orElseThrow();
				if (basedOn != activeVersion) {
					throw ApiException.conflict("PLAN_SUPERSEDED", "계획 " + planId + "는 활성 v" + basedOn
							+ " 기준으로 계산됐는데 지금 활성 계획은 v" + activeVersion
							+ "다. request_route_plan으로 새 후보를 받아 다시 승인을 받는다.");
				}
			}
			plans.closeActive(shiftId);
			plans.activate(planId, approvedBy, approvalId);
			audit.record(shiftId, AuditLog.Type.PLAN_ACTIVATED, AuditLog.Actor.approvingManager(approvedBy), planId, taskId,
					"v" + plan.version() + " · 승인 " + approvalId);
			return plans.find(planId).orElseThrow();
		});
		return toContract(activated);
	}

	/**
	 * propose_route_change. 후보의 이 트럭 경로를 활성 계획에 넣은 새 버전을 만든다. 다른 트럭은 활성 계획 그대로다.
	 * 등급은 후보 전체를 지금 활성 계획과 비교해 다시 매긴다 (카드에 적힌 등급은 믿지 않는다). 후보를 만들 때와 같은 비교라
	 * 카드의 승인 순서와 맞고, 한 트럭만 볼 때보다 느슨해지지 않는다 (트럭 간 이동은 MAJOR로 남는다).
	 */
	JsonNode apply(String truckId, UUID approvalId, UUID taskId, String candidateId) {
		String shiftId = plans.find(candidateId)
			.map(Plan::shiftId)
			.orElseThrow(() -> ApiException.conflict("CANDIDATE_NOT_FOUND",
					"후보 " + candidateId + "가 없다. request_route_plan(kind=REPLAN) 결과의 candidateId를 쓴다."));
		return tx.execute(s -> {
			plans.lockShift(shiftId);
			Optional<Plan> used = plans.activatedBy(approvalId);
			if (used.isPresent()) {
				Plan applied = used.get();
				if (!truckId.equals(applied.appliedTruckId()) || !candidateId.equals(applied.appliedCandidateId())) {
					throw alreadyUsed(approvalId, applied);
				}
				return routeOf(json.readTree(applied.routesJson()), truckId).orElseThrow(); // 재시도
			}
			Plan candidate = plans.find(candidateId).orElseThrow();
			if (!"PROPOSED".equals(candidate.status())) {
				throw ApiException.conflict("CANDIDATE_NOT_PROPOSED", "후보 " + candidateId + "는 " + candidate.status()
						+ " 상태라 적용할 수 없다. request_route_plan(kind=REPLAN)으로 새 후보를 받는다.");
			}
			JsonNode candidateRoutes = json.readTree(candidate.routesJson());
			JsonNode route = routeOf(candidateRoutes, truckId)
				.orElseThrow(() -> ApiException.conflict("TRUCK_NOT_IN_CANDIDATE", "후보 " + candidateId + "에 " + truckId
						+ " 경로가 없다. 후보의 트럭: " + truckIds(candidateRoutes)));
			Plan active = plans.activePlan(shiftId)
				.orElseThrow(() -> ApiException.conflict("NO_ACTIVE_PLAN", "근무조 " + shiftId
						+ "에 활성 계획이 없다. 경로 변경은 활성 계획에 적용한다. 먼저 submit_plan으로 계획을 활성화한다."));
			Integer basedOn = candidate.requestId() == null ? null
					: plans.basedOnVersion(candidate.requestId()).orElse(null);
			if (basedOn == null || basedOn != active.version()) {
				throw ApiException.conflict("STALE_CANDIDATE", "후보 " + candidateId + "는 활성 v" + basedOn
						+ " 기준으로 계산됐는데 지금 활성 계획은 v" + active.version()
						+ "다. request_route_plan(kind=REPLAN)으로 새 후보를 받아 다시 승인을 받는다.");
			}
			RouteChangeGrade grade = candidates.compare(active.routesJson(), candidateRoutes, taskId).grade();
			String driver = approvals.requireRouteChange(taskId, approvalId, truckId, candidateId, grade);

			int version = plans.nextVersion(shiftId);
			plans.closeActive(shiftId);
			plans.insertApplied("PL-" + UUID.randomUUID(), active, version,
					json.writeValueAsString(replaceRoute(json.readTree(active.routesJson()), route)), driver, approvalId,
					candidateId, truckId);
			audit.record(shiftId, AuditLog.Type.ROUTE_CHANGED, AuditLog.Actor.approvingDriver(driver, truckId), truckId,
					taskId, "후보 " + candidateId + " " + grade + " → v" + version + " · 승인 " + approvalId);
			return route.deepCopy();
		});
	}

	/** 계약 Plan. */
	private ObjectNode toContract(Plan p) {
		ObjectNode out = json.createObjectNode();
		out.put("planId", p.planId());
		out.put("shiftId", p.shiftId());
		out.put("version", p.version());
		out.put("status", p.status());
		out.put("provisional", p.provisional());
		out.set("routes", json.readTree(p.routesJson()));
		out.set("unserved", json.readTree(p.unservedJson()));
		out.put("explanation", p.explanation());
		return out;
	}

	/** 활성 계획의 경로 목록에서 그 트럭 경로를 바꾼다. 활성 계획에 없던 트럭이면 끝에 더한다. */
	private ArrayNode replaceRoute(JsonNode activeRoutes, JsonNode route) {
		String truckId = route.path("truckId").asString();
		ArrayNode out = json.createArrayNode();
		boolean replaced = false;
		for (JsonNode r : activeRoutes) {
			if (truckId.equals(r.path("truckId").asString())) {
				out.add(route.deepCopy());
				replaced = true;
			}
			else {
				out.add(r.deepCopy());
			}
		}
		if (!replaced) {
			out.add(route.deepCopy());
		}
		return out;
	}

	private static Optional<JsonNode> routeOf(JsonNode routes, String truckId) {
		for (JsonNode r : routes) {
			if (truckId.equals(r.path("truckId").asString())) {
				return Optional.of(r);
			}
		}
		return Optional.empty();
	}

	private static String truckIds(JsonNode routes) {
		return routes.valueStream().map(r -> r.path("truckId").asString()).collect(Collectors.joining(", "));
	}

	private static ApiException alreadyUsed(UUID approvalId, Plan used) {
		String what = used.appliedTruckId() == null ? "계획 " + used.planId() + " 활성화"
				: used.appliedTruckId() + " 경로 적용(후보 " + used.appliedCandidateId() + ")";
		return ApiException.conflict("APPROVAL_ALREADY_USED",
				"approvalId " + approvalId + "로 이미 " + what + "를 실행했다. 새 변경은 새 승인을 받는다.");
	}
}
