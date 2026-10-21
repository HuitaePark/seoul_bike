package com.seoulbike.backend.plan;

import java.time.Duration;
import java.time.Instant;
import java.time.format.DateTimeParseException;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;

import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Transactional;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ArrayNode;
import tools.jackson.databind.node.ObjectNode;

import com.seoulbike.backend.common.SeoulTime;
import com.seoulbike.backend.route.RouteChangeGrade;
import com.seoulbike.backend.route.RouteChangeGrader;
import com.seoulbike.backend.route.RouteDiff;

/**
 * 계산 결과로 계획 후보(PROPOSED)를 만들고 계약 PlanCandidate를 돌려준다. optimizer의 OK 결과와, 답을 받지 못했을 때 backend가
 * 만든 임시안(provisional, 후보 status TIMEOUT_PROVISIONAL)이 같은 길로 저장된다. 그래서 임시안도 같은 승인으로 활성화·적용된다.
 * 활성 계획이 있으면 트럭마다 비교해 변경 등급과 승인 순서를 붙인다. 없으면 첫 계획이라 등급이 없다
 * (활성화는 어차피 관리자의 SUBMIT_PLAN 승인을 거친다).
 */
@Component
class PlanCandidates {

	/**
	 * REPLAN이 기준으로 삼은 활성 계획. 후보는 그 계획의 전체 경로다: 다시 풀지 않은 트럭은 그대로, 다시 푼 트럭은 이미 끝낸
	 * 정류장(PENDING이 아닌 것)을 앞에 두고 새 정류장을 이어 붙인다. 그래야 끝낸 정류장과 손대지 않은 트럭이 빠진 것으로 비교되지 않는다.
	 *
	 * @param version 입력을 만든 활성 계획 version. 요청을 남길 때 활성 계획이 바뀌었으면 409 PLAN_CHANGED
	 * @param truckIds 다시 푼 트럭
	 */
	record Replan(int version, String activeRoutesJson, Set<String> truckIds) {
	}

	private final PlanRepository plans;
	private final SolveRequestRepository requests;
	private final RouteChangeGrader grader;
	private final JsonMapper json;

	PlanCandidates(PlanRepository plans, SolveRequestRepository requests, RouteChangeGrader grader, JsonMapper json) {
		this.plans = plans;
		this.requests = requests;
		this.grader = grader;
		this.json = json;
	}

	/**
	 * optimizer의 OK 결과.
	 *
	 * @param taskId 요청한 작업. 현장 보고 작업이면 FIELD_REPORT, 아니면(없어도) 관리자 요청으로 등급을 매긴다
	 * @param replan REPLAN이면 기준 계획, PLAN이면 null
	 */
	@Transactional
	ObjectNode create(String shiftId, String requestId, UUID taskId, JsonNode result, Replan replan) {
		return insert(shiftId, requestId, taskId, array(result.path("routes")), array(result.path("unserved")), replan,
				false, null);
	}

	/** optimizer가 답하지 못해 backend가 만든 임시안. 같은 요청(requestId)에 묶어 둔다. */
	@Transactional
	ObjectNode createProvisional(String shiftId, String requestId, UUID taskId, ArrayNode routes, ArrayNode unserved,
			Replan replan, String explanation) {
		return insert(shiftId, requestId, taskId, routes.deepCopy(), unserved.deepCopy(), replan, true, explanation);
	}

	private ObjectNode insert(String shiftId, String requestId, UUID taskId, ArrayNode solved, ArrayNode unserved,
			Replan replan, boolean provisional, String explanation) {
		plans.lockShift(shiftId);
		ArrayNode routes = seoulEtas(solved);
		if (replan != null) {
			routes = merge(json.readTree(replan.activeRoutesJson()), routes, replan.truckIds());
		}
		String planId = "PL-" + UUID.randomUUID();
		int version = plans.nextVersion(shiftId);
		plans.insertCandidate(planId, shiftId, version, provisional, json.writeValueAsString(routes),
				json.writeValueAsString(unserved), explanation, requestId);

		ObjectNode candidate = json.createObjectNode();
		candidate.put("candidateId", planId);
		candidate.put("status", provisional ? "TIMEOUT_PROVISIONAL" : "OK");
		ObjectNode plan = candidate.putObject("plan");
		plan.put("planId", planId);
		plan.put("shiftId", shiftId);
		plan.put("version", version);
		plan.put("status", "PROPOSED");
		plan.put("provisional", provisional);
		plan.set("routes", routes);
		plan.set("unserved", unserved);
		plan.put("explanation", explanation);

		ArrayNode approvers = json.createArrayNode();
		var active = plans.active(shiftId);
		if (active.isEmpty()) {
			candidate.putArray("diffs");
			candidate.putNull("grade");
		}
		else {
			Comparison comparison = compare(active.get().routesJson(), routes, taskId);
			candidate.set("diffs", comparison.diffs());
			candidate.put("grade", comparison.grade().name());
			comparison.grade().requiredApprovers().forEach(r -> approvers.add(r.name()));
		}
		candidate.set("requiredApprovers", approvers);
		return candidate;
	}

	/** 활성 계획의 트럭 순서대로. 다시 푼 트럭은 끝낸 정류장 + 새 정류장(seq를 이어 매긴다), 출발 적재량은 활성 계획 그대로. */
	private ArrayNode merge(JsonNode activeRoutes, ArrayNode solved, Set<String> truckIds) {
		Map<String, JsonNode> byTruck = new LinkedHashMap<>();
		solved.forEach(r -> byTruck.put(r.path("truckId").asString(), r));
		ArrayNode out = json.createArrayNode();
		for (JsonNode active : activeRoutes) {
			String truckId = active.path("truckId").asString();
			if (!truckIds.contains(truckId)) {
				out.add(active.deepCopy());
				continue;
			}
			ObjectNode route = (ObjectNode) active.deepCopy();
			ArrayNode stops = route.putArray("stops");
			for (JsonNode stop : active.path("stops")) {
				if (!"PENDING".equals(stop.path("status").asString("PENDING"))) {
					stops.add(stop.deepCopy());
				}
			}
			JsonNode next = byTruck.remove(truckId);
			if (next != null) {
				for (JsonNode stop : next.path("stops")) {
					stops.add(((ObjectNode) stop.deepCopy()).put("seq", stops.size() + 1));
				}
			}
			out.add(route);
		}
		byTruck.values().forEach(r -> out.add(r.deepCopy()));
		return out;
	}

	/** 활성 계획 대비 트럭별 변경(계약 RouteDiff 배열)과 등급. */
	record Comparison(ArrayNode diffs, RouteChangeGrade grade) {
	}

	/**
	 * 후보 경로를 활성 계획과 비교해 등급을 매긴다. 후보를 만들 때와 경로를 적용할 때 같은 규칙을 쓴다
	 * (적용 API는 카드에 적힌 등급을 믿지 않고 여기서 다시 계산한다).
	 *
	 * @param taskId 요청한 작업. 현장 보고 작업이면 FIELD_REPORT, 아니면(없어도) 관리자 요청으로 등급을 매긴다
	 */
	Comparison compare(String activeRoutesJson, JsonNode candidateRoutes, UUID taskId) {
		ArrayNode diffsOut = json.createArrayNode();
		List<RouteDiff> diffs = new ArrayList<>();
		for (Map.Entry<String, JsonNode[]> truck : byTruck(json.readTree(activeRoutesJson), candidateRoutes).entrySet()) {
			JsonNode before = truck.getValue()[0];
			JsonNode after = truck.getValue()[1];
			double addedMinutes = addedMinutes(before, after);
			diffs.add(new RouteDiff(truck.getKey(), gradeStops(before), gradeStops(after), addedMinutes));
			ObjectNode diff = diffsOut.addObject();
			diff.put("truckId", truck.getKey());
			diff.set("before", before.deepCopy());
			diff.set("after", after.deepCopy());
			diff.put("addedMinutes", addedMinutes);
		}
		RouteDiff.Origin origin = taskId != null && requests.fromFieldReport(taskId) ? RouteDiff.Origin.FIELD_REPORT
				: RouteDiff.Origin.MANAGER_REQUEST;
		return new Comparison(diffsOut, grader.grade(diffs, origin));
	}

	/** 활성 계획과 후보 중 어느 쪽에든 있는 트럭 → [before 정류장, after 정류장]. 활성 계획의 트럭 순서를 따른다. */
	private Map<String, JsonNode[]> byTruck(JsonNode activeRoutes, JsonNode candidateRoutes) {
		Map<String, JsonNode[]> trucks = new LinkedHashMap<>();
		for (JsonNode route : activeRoutes) {
			trucks.computeIfAbsent(route.path("truckId").asString(), t -> empty())[0] = array(route.path("stops"));
		}
		for (JsonNode route : candidateRoutes) {
			trucks.computeIfAbsent(route.path("truckId").asString(), t -> empty())[1] = array(route.path("stops"));
		}
		return trucks;
	}

	private JsonNode[] empty() {
		return new JsonNode[] { json.createArrayNode(), json.createArrayNode() };
	}

	/** optimizer는 eta를 UTC로 준다. 계획 API·승인 카드가 이 저장 값을 그대로 보내므로 저장 전에 +09:00으로 바꾼다. */
	private static ArrayNode seoulEtas(ArrayNode routes) {
		for (JsonNode route : routes) {
			for (JsonNode stop : route.path("stops")) {
				JsonNode eta = stop.get("eta");
				if (eta != null && eta.isString()) {
					((ObjectNode) stop).put("eta", SeoulTime.normalize(eta.asString()));
				}
			}
		}
		return routes;
	}

	private ArrayNode array(JsonNode node) {
		return node.isArray() ? (ArrayNode) node.deepCopy() : json.createArrayNode();
	}

	private static List<RouteDiff.Stop> gradeStops(JsonNode stops) {
		List<RouteDiff.Stop> out = new ArrayList<>();
		for (JsonNode s : stops) {
			out.add(new RouteDiff.Stop(s.path("stationId").asString(), RouteDiff.Action.valueOf(s.path("action").asString()),
					s.path("bikes").asInt()));
		}
		return out;
	}

	/** 마지막 정류장 도착 시각의 차이(after − before, 분). 한쪽이라도 정류장이나 eta가 없으면 0. */
	private static double addedMinutes(JsonNode before, JsonNode after) {
		Instant b = lastEta(before);
		Instant a = lastEta(after);
		if (b == null || a == null) {
			return 0;
		}
		return Duration.between(b, a).toSeconds() / 60.0;
	}

	private static Instant lastEta(JsonNode stops) {
		if (stops.isEmpty()) {
			return null;
		}
		String eta = stops.get(stops.size() - 1).path("eta").asString(null);
		if (eta == null) {
			return null;
		}
		try {
			return Instant.parse(eta);
		}
		catch (DateTimeParseException e) {
			throw new IllegalArgumentException("Stop.eta가 RFC 3339 시각이 아니다: " + eta, e);
		}
	}
}
