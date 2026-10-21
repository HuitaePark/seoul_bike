package com.seoulbike.backend.plan;

import java.time.Instant;
import java.time.temporal.ChronoUnit;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.TreeSet;
import java.util.UUID;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ObjectNode;

import com.seoulbike.backend.common.ApiException;
import com.seoulbike.backend.common.SeoulTime;
import com.seoulbike.backend.station.StationNeeds;

/**
 * business-mcp `request_route_plan` (internal-api POST /internal/plans/solve). 근무조의 대여소·트럭·제약으로 계약 SolveRequest
 * 입력을 만들어 optimizer에 보내고(SolveGateway), 답을 받지 못하면 같은 입력으로 탐욕 임시안(GreedyPlanner)을 만든다.
 * 입력을 만드는 규칙은 internal-api 이 경로의 설명에 적은 것과 같다.
 *
 * <p>트랜잭션 밖에서 돈다. 결과를 최대 app.solve.timeout 기다리는 동안 DB 연결을 붙잡지 않는다.
 */
@Service
class PlanSolver {

	private static final Logger log = LoggerFactory.getLogger(PlanSolver.class);

	/** 위험 판단 구간. get_risk_stations 기본값과 같다 (합의 16, FR-01). */
	static final int HORIZON_H = 3;
	/** 합의 19. */
	static final double SERVICE_MINUTES_BASE = 3;
	static final double SERVICE_MINUTES_PER_BIKE = 0.5;
	/** 관리자 지정 우선(PRIORITY_BOOST)은 우선순위를 이만큼 곱한다. 제약이 여러 개여도 한 번이다. */
	static final double PRIORITY_BOOST = 2;

	/** 계약 internal-api SolveRequest. */
	record Command(String shiftId, SolveGateway.Kind kind, List<String> truckIds, List<String> excludeStationIds,
			UUID taskId) {
	}

	/** 근무조 제약을 계산에 쓰는 꼴로 모은 것. */
	private record Constraints(Map<String, Integer> inventory, Map<String, Integer> targetAdjust, Set<String> boosted,
			Set<String> blocked) {
	}

	/** 트럭과 REPLAN 기준. stationsOut은 이번 계산에서 뺄 대여소(끝낸 정류장, 다시 풀지 않는 트럭의 남은 정류장). */
	private record Fleet(List<SolveInput.Truck> trucks, PlanCandidates.Replan replan, Set<String> stationsOut) {
	}

	private final SolveGateway gateway;
	private final PlanCandidates candidates;
	private final PlanRepository plans;
	private final SolveInputRepository inputs;
	private final StationNeeds needs;
	private final JsonMapper json;
	private final int solutionLimit;

	PlanSolver(SolveGateway gateway, PlanCandidates candidates, PlanRepository plans, SolveInputRepository inputs,
			StationNeeds needs, JsonMapper json, @Value("${app.solve.solution-limit}") int solutionLimit) {
		this.gateway = gateway;
		this.candidates = candidates;
		this.plans = plans;
		this.inputs = inputs;
		this.needs = needs;
		this.json = json;
		this.solutionLimit = solutionLimit;
	}

	/** 계약 PlanCandidate. */
	ObjectNode solve(Command cmd) {
		Shift shift = Shift.parse(cmd.shiftId());
		SolveInput.Point depot = inputs.depot(shift.regionId())
			.orElseThrow(() -> ApiException.notFound("SHIFT_NOT_FOUND", "근무조 " + shift.id() + "의 구역 "
					+ shift.regionId() + "이 없다. 구역 ID는 강북 N01~N08, 강남 S01~S09다."));
		String traceId = null;
		if (cmd.taskId() != null) {
			traceId = inputs.task(cmd.taskId())
				.orElseThrow(() -> new ApiException(HttpStatus.BAD_REQUEST, "INVALID_REQUEST",
						"requestedBy.taskId " + cmd.taskId() + " 작업이 없다. 이 계산을 요청한 작업의 ID를 넣거나 requestedBy를 뺀다."))
				.traceId();
		}
		// 근무조 전이면 트럭은 근무조 시작에 출발한다. 끝난 근무조는 계산할 것이 없다 (optimizer도 거부한다).
		Instant asOf = max(Instant.now(), shift.start()).truncatedTo(ChronoUnit.SECONDS);
		if (!asOf.isBefore(shift.end())) {
			throw ApiException.conflict("SHIFT_ENDED", "근무조 " + shift.id() + "는 " + SeoulTime.format(shift.end()) + "에 끝났다. 다음 근무조로 계산한다.");
		}
		List<String> excluded = cmd.excludeStationIds() == null ? List.of() : cmd.excludeStationIds();
		needs.requireActive(excluded);

		Constraints constraints = constraints(shift, asOf);
		Fleet fleet = cmd.kind() == SolveGateway.Kind.PLAN ? planFleet(shift, depot)
				: replanFleet(shift, depot, cmd.truckIds());

		List<SolveInput.Station> stations = new ArrayList<>();
		for (StationNeeds.Need n : needs.region(shift.regionId(), HORIZON_H, constraints.inventory(),
				constraints.targetAdjust())) {
			if (n.neededBikes() == 0 || fleet.stationsOut().contains(n.stationId())) {
				continue;
			}
			double priority = constraints.boosted().contains(n.stationId())
					? Math.round(n.priority() * PRIORITY_BOOST * 100) / 100.0 : n.priority();
			stations.add(new SolveInput.Station(n.stationId(), n.lat(), n.lon(), n.neededBikes(), priority,
					n.neededBikes() < 0 ? "EMPTY" : "OVERFLOW"));
		}
		// optimizer는 stations에 없는 ID를 막을 수 없다고 거부한다 (optimizer.md). 막을 대여소가 계산 대상일 때만 넣는다.
		Set<String> stationIds = new HashSet<>();
		stations.forEach(s -> stationIds.add(s.stationId()));
		Set<String> blocked = new TreeSet<>(constraints.blocked());
		blocked.addAll(excluded);
		blocked.retainAll(stationIds);
		List<SolveInput.Truck> trucks = fixStations(fleet.trucks(), stationIds, blocked);

		SolveInput input = new SolveInput(depot, asOf, stations, trucks, List.copyOf(blocked),
				new SolveInput.Params(solutionLimit, SERVICE_MINUTES_BASE, SERVICE_MINUTES_PER_BIKE));
		SolveOutcome outcome = gateway.solve(shift.id(), cmd.kind(), json.valueToTree(input), cmd.taskId(), traceId,
				fleet.replan());
		if (outcome.status() == SolveOutcome.Status.OK) {
			return outcome.candidate();
		}
		log.warn("경로 계산 {}이 {}({}) — 탐욕 임시안을 만든다: {}", outcome.requestId(), outcome.status(), outcome.code(),
				outcome.error());
		GreedyPlanner.Result greedy = GreedyPlanner.plan(input);
		String why = outcome.status() == SolveOutcome.Status.TIMED_OUT ? "최적화가 제한 시간 안에 답하지 않아"
				: "최적화가 경로를 내지 못해(" + outcome.code() + ")";
		return candidates.createProvisional(shift.id(), outcome.requestId(), cmd.taskId(),
				json.valueToTree(greedy.routes()), json.valueToTree(greedy.unserved()), fleet.replan(),
				why + " 가까운 순 탐욕 규칙으로 만든 임시안이다. 최적화 결과가 아니므로 다시 계산해 볼 수 있다.");
	}

	/** PLAN: 구역의 운영 중인 트럭 전부가 차고지에서 빈 채로 출발한다. */
	private Fleet planFleet(Shift shift, SolveInput.Point depot) {
		List<SolveInput.Truck> trucks = inputs.activeTrucks(shift.regionId())
			.stream()
			.map(t -> new SolveInput.Truck(t.truckId(), t.capacity(), 0, depot.lat(), depot.lon(), shift.end(), List.of()))
			.toList();
		return new Fleet(trucks, null, Set.of());
	}

	/**
	 * REPLAN: 활성 계획의 트럭(truckIds로 거른다). 위치는 마지막으로 끝낸(DONE) 정류장, 없으면 차고지. 적재는 그 정류장의 loadAfter,
	 * 없으면 경로의 startLoad. 남은(PENDING) 정류장은 그 트럭의 fixedStationIds 후보다.
	 */
	private Fleet replanFleet(Shift shift, SolveInput.Point depot, List<String> truckIds) {
		PlanRepository.Plan active = plans.activePlan(shift.id())
			.orElseThrow(() -> ApiException.notFound("SHIFT_NOT_FOUND", "근무조 " + shift.id()
					+ "에 활성 계획이 없다. 재경로(REPLAN)는 활성 계획에서 시작한다. kind=PLAN으로 계획을 만들고 submit_plan으로 활성화한다."));
		JsonNode routes = json.readTree(active.routesJson());
		List<String> planned = new ArrayList<>();
		routes.forEach(r -> planned.add(r.path("truckId").asString()));
		Set<String> chosen = new LinkedHashSet<>(truckIds == null || truckIds.isEmpty() ? planned : truckIds);
		List<String> unknown = chosen.stream().filter(t -> !planned.contains(t)).toList();
		if (!unknown.isEmpty()) {
			throw ApiException.notFound("TRUCK_NOT_FOUND", "근무조 " + shift.id() + "의 활성 계획에 없는 트럭: "
					+ String.join(", ", unknown) + ". 계획의 트럭: " + String.join(", ", planned) + ".", planned);
		}

		Set<String> out = new HashSet<>();
		Map<String, JsonNode> lastDone = new HashMap<>();
		for (JsonNode route : routes) {
			String truckId = route.path("truckId").asString();
			for (JsonNode stop : route.path("stops")) {
				String status = stop.path("status").asString("PENDING");
				if ("DONE".equals(status)) {
					out.add(stop.path("stationId").asString());
					lastDone.put(truckId, stop);
				}
				else if ("PENDING".equals(status) && !chosen.contains(truckId)) {
					out.add(stop.path("stationId").asString());
				}
			}
		}
		Set<String> doneStations = new HashSet<>();
		lastDone.values().forEach(s -> doneStations.add(s.path("stationId").asString()));
		Map<String, SolveInput.Point> positions = inputs.positions(doneStations);

		List<SolveInput.Truck> trucks = new ArrayList<>();
		for (JsonNode route : routes) {
			String truckId = route.path("truckId").asString();
			if (!chosen.contains(truckId)) {
				continue;
			}
			JsonNode done = lastDone.get(truckId);
			SolveInput.Point at = done == null ? depot : positions.getOrDefault(done.path("stationId").asString(), depot);
			int load = done != null && done.has("loadAfter") ? done.path("loadAfter").asInt()
					: route.path("startLoad").asInt(0);
			List<String> pending = new ArrayList<>();
			for (JsonNode stop : route.path("stops")) {
				if ("PENDING".equals(stop.path("status").asString("PENDING"))) {
					pending.add(stop.path("stationId").asString());
				}
			}
			trucks.add(new SolveInput.Truck(truckId, route.path("capacity").asInt(15), load, at.lat(), at.lon(), shift.end(),
					pending));
		}
		return new Fleet(trucks, new PlanCandidates.Replan(active.version(), active.routesJson(), chosen), out);
	}

	/**
	 * fixedStationIds는 계산 대상이고 막히지 않은 대여소만 남긴다 (optimizer는 모르는 ID와 중복 고정을 거부한다).
	 * 두 트럭에 같은 대여소가 남아 있으면 앞 트럭에 둔다.
	 */
	private static List<SolveInput.Truck> fixStations(List<SolveInput.Truck> trucks, Set<String> stationIds,
			Set<String> blocked) {
		Set<String> taken = new HashSet<>();
		List<SolveInput.Truck> out = new ArrayList<>();
		for (SolveInput.Truck t : trucks) {
			List<String> fixed = t.fixedStationIds()
				.stream()
				.filter(id -> stationIds.contains(id) && !blocked.contains(id) && taken.add(id))
				.toList();
			out.add(new SolveInput.Truck(t.truckId(), t.capacity(), t.load(), t.lat(), t.lon(), t.availableUntil(), fixed));
		}
		return out;
	}

	/**
	 * 이 근무조의 제약 중 [asOf, 근무조 끝)과 겹치는 것. STATION_SUSPENDED·STATION_BLOCKED는 막을 대여소,
	 * INVENTORY_OVERRIDE는 위험 판단 전 재고(같은 대여소에 여럿이면 가장 늦게 시작한 것), TARGET_ADJUST는 가감 대수 합,
	 * PRIORITY_BOOST는 우선순위 2배.
	 */
	private Constraints constraints(Shift shift, Instant asOf) {
		Map<String, Integer> inventory = new HashMap<>();
		Map<String, Integer> adjust = new HashMap<>();
		Set<String> boosted = new HashSet<>();
		Set<String> blocked = new HashSet<>();
		for (SolveInputRepository.ConstraintRow c : inputs.constraints(shift.id(), asOf, shift.end())) {
			for (String id : c.stationIds()) {
				switch (c.type()) {
					case "STATION_SUSPENDED", "STATION_BLOCKED" -> blocked.add(id);
					case "INVENTORY_OVERRIDE" -> inventory.putIfAbsent(id, c.value());
					case "TARGET_ADJUST" -> adjust.merge(id, c.value(), Integer::sum);
					case "PRIORITY_BOOST" -> boosted.add(id);
					default -> throw new IllegalStateException("모르는 제약 종류: " + c.type());
				}
			}
		}
		return new Constraints(inventory, adjust, boosted, blocked);
	}

	private static Instant max(Instant a, Instant b) {
		return a.isAfter(b) ? a : b;
	}
}
