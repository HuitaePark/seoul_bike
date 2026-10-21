package com.seoulbike.backend.plan;

import java.util.Optional;

import org.springframework.stereotype.Component;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ObjectNode;

/**
 * 기사 현장 보고로 활성 계획의 정류장 진행(계약 Stop.status)을 남긴다. 트럭 상태(TruckStates)와 재경로(PlanSolver REPLAN)는
 * 마지막으로 끝낸(DONE) 정류장에서 출발하므로, 진행을 남기지 않으면 트럭은 근무조 내내 차고지에 빈 채로 있다.
 *
 * <p>진행은 경로를 바꾸지 않으므로 새 버전을 만들지 않고 승인도 받지 않는다 (무승인 변경으로 세지 않는다). 같은 근무조의 활성화·경로
 * 적용과 겹치지 않게 근무조 잠금 안에서 고친다. 부르는 쪽 트랜잭션 안에서 부른다.
 */
@Component
public class RouteProgress {

	private final PlanRepository plans;
	private final JsonMapper json;

	RouteProgress(PlanRepository plans, JsonMapper json) {
		this.plans = plans;
		this.json = json;
	}

	/**
	 * 그 근무조 활성 계획의 이 트럭 경로에서 이 대여소의 첫 남은(PENDING) 정류장을 끝낸다. 처리했으면 DONE, 처리하지 못했으면 SKIPPED.
	 * DONE이고 실은·내린 대수를 보고했으면 적재(loadAfter)를 실제 값으로 다시 센다: 직전에 끝낸 정류장의 적재(없으면 출발 적재)
	 * + 실은 대수 − 내린 대수. 보고하지 않았으면 계획의 적재를 그대로 둔다.
	 *
	 * @return 진행을 남긴 정류장 순번. 활성 계획이 없거나 그 트럭 경로에 남은 정류장이 아니면 empty (보고는 그대로 받는다)
	 */
	public Optional<Integer> record(String shiftId, String truckId, String stationId, boolean served, Integer loaded,
			Integer unloaded) {
		if (truckId == null) {
			return Optional.empty();
		}
		plans.lockShift(shiftId);
		Optional<PlanRepository.Plan> active = plans.activePlan(shiftId);
		if (active.isEmpty()) {
			return Optional.empty();
		}
		JsonNode routes = json.readTree(active.get().routesJson());
		for (JsonNode route : routes) {
			if (!truckId.equals(route.path("truckId").asString())) {
				continue;
			}
			int load = route.path("startLoad").asInt(0);
			for (JsonNode node : route.path("stops")) {
				String status = node.path("status").asString("PENDING");
				if ("DONE".equals(status)) {
					load = node.path("loadAfter").asInt(load);
				}
				if (!"PENDING".equals(status) || !stationId.equals(node.path("stationId").asString())) {
					continue;
				}
				ObjectNode stop = (ObjectNode) node;
				stop.put("status", served ? "DONE" : "SKIPPED");
				if (served && (loaded != null || unloaded != null)) {
					stop.put("loadAfter", Math.max(0, load + (loaded == null ? 0 : loaded) - (unloaded == null ? 0 : unloaded)));
				}
				plans.updateRoutes(active.get().planId(), json.writeValueAsString(routes));
				return Optional.of(stop.path("seq").asInt());
			}
		}
		return Optional.empty();
	}
}
