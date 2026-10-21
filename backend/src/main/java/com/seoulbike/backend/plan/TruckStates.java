package com.seoulbike.backend.plan;

import java.time.Instant;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Set;

import org.springframework.stereotype.Component;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

import com.seoulbike.backend.common.ApiException;

/**
 * 근무조 트럭의 지금 상태 (계약 internal-api TruckState). 트럭 상태 조회(`get_truck_status`)와 재경로 입력(PlanSolver REPLAN)이
 * 같은 규칙을 쓰도록 여기 한 곳에서 계산한다. 둘이 어긋나면 에이전트가 본 위치·적재와 다른 상태로 다시 계산한다.
 *
 * <p>활성 계획이 있으면 그 계획의 트럭(계획 순서): 위치는 마지막으로 끝낸(DONE) 정류장, 없으면 차고지. 적재는 그 정류장의 loadAfter,
 * 없으면 경로의 startLoad. 남은 정류장은 PENDING인 것. 활성 계획이 없으면 구역의 운영 중인 트럭(트럭 ID 순)이 차고지에 빈 채로 있다
 * (계획 초안 PLAN의 출발 상태와 같다).
 */
@Component
class TruckStates {

	/** @param remainingStops 남은(PENDING) 정류장. 계약 Stop 그대로 */
	record State(String truckId, int capacity, int load, double lat, double lon, Instant shiftEnd,
			List<JsonNode> remainingStops) {

		List<String> remainingStationIds() {
			return remainingStops.stream().map(s -> s.path("stationId").asString()).toList();
		}
	}

	private final PlanRepository plans;
	private final SolveInputRepository inputs;
	private final JsonMapper json;

	TruckStates(PlanRepository plans, SolveInputRepository inputs, JsonMapper json) {
		this.plans = plans;
		this.inputs = inputs;
		this.json = json;
	}

	/**
	 * GET /internal/shifts/{shiftId}/trucks. 형식이 틀린 근무조는 400, 없는 구역은 404 SHIFT_NOT_FOUND.
	 *
	 * @param truckId 있으면 그 트럭만. 근무조에 없으면 404 TRUCK_NOT_FOUND (`suggestions`에 근무조 트럭)
	 */
	List<State> status(String shiftId, String truckId) {
		Shift shift = Shift.parse(shiftId);
		SolveInput.Point depot = depot(shift);
		List<State> all = plans.activePlan(shift.id())
			.map(p -> fromPlan(shift, depot, json.readTree(p.routesJson())))
			.orElseGet(() -> atDepot(shift, depot));
		if (truckId == null || truckId.isBlank()) {
			return all;
		}
		List<String> ids = all.stream().map(State::truckId).toList();
		return all.stream()
			.filter(s -> s.truckId().equals(truckId))
			.findFirst()
			.map(List::of)
			.orElseThrow(() -> ApiException.notFound("TRUCK_NOT_FOUND", "'" + truckId + "' 트럭이 근무조 " + shift.id()
					+ "에 없다. 근무조 트럭: " + String.join(", ", ids) + ".", ids));
	}

	SolveInput.Point depot(Shift shift) {
		return inputs.depot(shift.regionId())
			.orElseThrow(() -> ApiException.notFound("SHIFT_NOT_FOUND", "근무조 " + shift.id() + "의 구역 "
					+ shift.regionId() + "이 없다. 구역 ID는 강북 N01~N08, 강남 S01~S09다."));
	}

	/** 활성 계획이 없을 때: 구역의 운영 중인 트럭이 차고지에 빈 채로. */
	List<State> atDepot(Shift shift, SolveInput.Point depot) {
		return inputs.activeTrucks(shift.regionId())
			.stream()
			.map(t -> new State(t.truckId(), t.capacity(), 0, depot.lat(), depot.lon(), shift.end(), List.of()))
			.toList();
	}

	/** 활성 계획의 경로(계약 Route 배열)로. 계획 순서대로. */
	List<State> fromPlan(Shift shift, SolveInput.Point depot, JsonNode routes) {
		Set<String> doneStations = new HashSet<>();
		for (JsonNode route : routes) {
			JsonNode done = lastDone(route);
			if (done != null) {
				doneStations.add(done.path("stationId").asString());
			}
		}
		var positions = inputs.positions(doneStations);
		List<State> out = new ArrayList<>();
		for (JsonNode route : routes) {
			JsonNode done = lastDone(route);
			// 끝낸 정류장이 기준 정보에서 빠졌으면 좌표를 모른다. 차고지로 둔다.
			SolveInput.Point at = done == null ? depot : positions.getOrDefault(done.path("stationId").asString(), depot);
			int load = done != null && done.has("loadAfter") ? done.path("loadAfter").asInt()
					: route.path("startLoad").asInt(0);
			List<JsonNode> pending = new ArrayList<>();
			for (JsonNode stop : route.path("stops")) {
				if (pending(stop)) {
					pending.add(stop.deepCopy());
				}
			}
			out.add(new State(route.path("truckId").asString(), route.path("capacity").asInt(15), load, at.lat(), at.lon(),
					shift.end(), List.copyOf(pending)));
		}
		return out;
	}

	static boolean pending(JsonNode stop) {
		return "PENDING".equals(stop.path("status").asString("PENDING"));
	}

	/** 경로에서 마지막으로 나오는 DONE 정류장. 없으면 null. */
	private static JsonNode lastDone(JsonNode route) {
		JsonNode last = null;
		for (JsonNode stop : route.path("stops")) {
			if ("DONE".equals(stop.path("status").asString("PENDING"))) {
				last = stop;
			}
		}
		return last;
	}
}
