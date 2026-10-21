package com.seoulbike.backend.plan;

import static org.assertj.core.api.Assertions.assertThat;

import java.time.Instant;
import java.util.List;

import org.junit.jupiter.api.Test;

/** 시간 초과 임시안. 합의 19 운행 파라미터로 용량·적재·근무조 끝을 지키는지 본다. */
class GreedyPlannerTest {

	private static final Instant AS_OF = Instant.parse("2026-10-21T22:00:00Z");
	private static final SolveInput.Point DEPOT = new SolveInput.Point(37.5, 127.0);
	private static final SolveInput.Params PARAMS = new SolveInput.Params(50, 3, 0.5);

	@Test
	void 빈_트럭은_회수부터_하고_용량을_넘기지_않으며_실은_만큼만_배치한다() {
		var result = GreedyPlanner.plan(input(List.of(
				station("ST-1", 0.001, 12), station("ST-2", 0.002, 10), station("ST-3", 0.003, -30)),
				List.of(truck("TR-01", 0, 600)), List.of()));

		List<GreedyPlanner.Stop> stops = result.routes().getFirst().stops();
		assertThat(stops).extracting(GreedyPlanner.Stop::stationId).containsExactly("ST-1", "ST-2", "ST-3");
		assertThat(stops).extracting(GreedyPlanner.Stop::action).containsExactly("PICKUP", "PICKUP", "DROPOFF");
		// 용량 15: 12대 싣고 남은 3대만 더 싣는다. 배치는 실은 15대까지.
		assertThat(stops).extracting(GreedyPlanner.Stop::bikes).containsExactly(12, 3, 15);
		assertThat(stops).extracting(GreedyPlanner.Stop::loadAfter).containsExactly(12, 15, 0);
		assertThat(stops).extracting(GreedyPlanner.Stop::seq).containsExactly(1, 2, 3);
		assertThat(result.unserved()).isEmpty();
	}

	@Test
	void 도착_시각은_직선거리_1_3배를_시속_20km로_달리고_정차는_3분_더하기_대당_30초다() {
		var result = GreedyPlanner.plan(input(List.of(station("ST-1", 0.01, 4), station("ST-2", 0.02, 4)),
				List.of(truck("TR-01", 0, 600)), List.of()));

		List<GreedyPlanner.Stop> stops = result.routes().getFirst().stops();
		// 위도 0.01도 ≈ 1.112km → × 1.3 ÷ 20km/h ≈ 260.2초 → 올림 261초.
		long leg = GreedyPlanner.travelSeconds(37.5, 127.0, 37.51, 127.0);
		assertThat(leg).isEqualTo(261);
		assertThat(stops.get(0).eta()).isEqualTo(AS_OF.plusSeconds(leg));
		long service = (long) ((3 + 0.5 * 4) * 60);
		assertThat(stops.get(1).eta()).isEqualTo(
				AS_OF.plusSeconds(leg + service + GreedyPlanner.travelSeconds(37.51, 127.0, 37.52, 127.0)));
	}

	@Test
	void 차고지로_돌아올_시간이_없는_대여소는_가지_않고_미처리로_남긴다() {
		// 0.1도(약 11km): 가는 데만 43분이 넘어 30분 근무조로는 못 간다.
		var result = GreedyPlanner.plan(input(List.of(station("ST-1", 0.001, 3), station("ST-9", 0.1, 3)),
				List.of(truck("TR-01", 0, 30)), List.of()));

		assertThat(result.routes().getFirst().stops()).extracting(GreedyPlanner.Stop::stationId).containsExactly("ST-1");
		assertThat(result.unserved()).containsExactly(new GreedyPlanner.Unserved("ST-9", 1.0, "NOT_SELECTED"));
	}

	@Test
	void 막힌_대여소는_가지_않고_고정된_대여소는_그_트럭만_간다() {
		SolveInput.Truck near = truck("TR-01", 0, 600);
		SolveInput.Truck owner = new SolveInput.Truck("TR-02", 15, 0, DEPOT.lat() + 0.05, DEPOT.lon(),
				AS_OF.plusSeconds(600 * 60), List.of("ST-2"));
		var result = GreedyPlanner.plan(input(List.of(station("ST-1", 0.001, 3), station("ST-2", 0.002, 3)),
				List.of(near, owner), List.of("ST-1")));

		assertThat(result.routes()).extracting(GreedyPlanner.Route::truckId).containsExactly("TR-01", "TR-02");
		assertThat(result.routes().get(0).stops()).isEmpty();
		assertThat(result.routes().get(1).stops()).extracting(GreedyPlanner.Stop::stationId).containsExactly("ST-2");
		assertThat(result.unserved()).containsExactly(new GreedyPlanner.Unserved("ST-1", 1.0, "BLOCKED"));
	}

	@Test
	void 트럭이_없으면_모두_NO_TRUCKS이고_실은_자전거가_없으면_배치하지_않는다() {
		assertThat(GreedyPlanner.plan(input(List.of(station("ST-1", 0.001, 3)), List.of(), List.of())).unserved())
			.containsExactly(new GreedyPlanner.Unserved("ST-1", 1.0, "NO_TRUCKS"));

		var empty = GreedyPlanner.plan(input(List.of(station("ST-1", 0.001, -3)), List.of(truck("TR-01", 0, 600)),
				List.of()));
		assertThat(empty.routes().getFirst().stops()).isEmpty();
		var loaded = GreedyPlanner.plan(input(List.of(station("ST-1", 0.001, -3)), List.of(truck("TR-01", 2, 600)),
				List.of()));
		assertThat(loaded.routes().getFirst().startLoad()).isEqualTo(2);
		assertThat(loaded.routes().getFirst().stops()).extracting(GreedyPlanner.Stop::bikes).containsExactly(2);
	}

	private static SolveInput input(List<SolveInput.Station> stations, List<SolveInput.Truck> trucks,
			List<String> blocked) {
		return new SolveInput(DEPOT, AS_OF, stations, trucks, blocked, PARAMS);
	}

	/** 차고지에서 북쪽으로 dLat도. 우선순위는 1. */
	private static SolveInput.Station station(String id, double dLat, int needed) {
		return new SolveInput.Station(id, DEPOT.lat() + dLat, DEPOT.lon(), needed, 1.0, needed < 0 ? "EMPTY" : "OVERFLOW");
	}

	private static SolveInput.Truck truck(String id, int load, int minutes) {
		return new SolveInput.Truck(id, 15, load, DEPOT.lat(), DEPOT.lon(), AS_OF.plusSeconds(minutes * 60L), List.of());
	}
}
