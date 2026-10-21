package com.seoulbike.backend.plan;

import java.time.Duration;
import java.time.Instant;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * optimizer가 답하지 못했을 때 backend가 만드는 임시안 (internal-api /internal/plans/solve, `provisional: true`).
 * 가까운 순 탐욕 규칙(PRD 9.1 "단순 규칙")이다. 같은 입력이면 같은 결과다 (NFR-04).
 *
 * <p>운행 파라미터는 합의 19: 직선거리 × 1.3을 시속 20km로 달리고, 정차는 serviceMinutesBase + 대당 serviceMinutesPerBike분.
 * 트럭은 다음 대여소에 들러 작업하고 차고지로 돌아와도 availableUntil 안일 때만 그 대여소를 고른다.
 *
 * <p>규칙: 지금 시각이 가장 이른 트럭(같으면 입력 순서)이 다음 대여소를 고른다. 고를 수 있는 대여소는 막히지 않았고,
 * 다른 트럭에 고정되지 않았고, 지금 적재로 작업할 수 있는(회수는 빈 자리, 배치는 실은 자전거가 있는) 곳이다. 그중 가장 가까운 곳
 * (같으면 우선순위가 높은 곳, 그다음 대여소 ID 순). 작업 대수는 필요 대수와 적재 여유 중 작은 값이고, 한 대여소는 한 번만 간다
 * (optimizer와 같다: 재방문 없음). 더 고를 곳이 없으면 그 트럭은 끝난다. 끝까지 아무 트럭도 들르지 않은 대여소는 미처리다.
 */
final class GreedyPlanner {

	static final double DETOUR = 1.3;
	static final double SPEED_KMH = 20;
	private static final double EARTH_KM = 6371.0088;

	record Stop(int seq, String stationId, String action, int bikes, int loadAfter, Instant eta, String status) {
	}

	record Route(String truckId, int capacity, int startLoad, List<Stop> stops) {
	}

	record Unserved(String stationId, double priority, String reason) {
	}

	record Result(List<Route> routes, List<Unserved> unserved) {
	}

	private static final class Truck {
		final SolveInput.Truck in;
		final List<Stop> stops = new ArrayList<>();
		double lat;
		double lon;
		Instant time;
		int load;
		boolean finished;

		Truck(SolveInput.Truck in, Instant asOf) {
			this.in = in;
			this.lat = in.lat();
			this.lon = in.lon();
			this.time = asOf;
			this.load = in.load();
		}
	}

	private GreedyPlanner() {
	}

	static Result plan(SolveInput in) {
		Set<String> blocked = new HashSet<>(in.blockedStationIds());
		Map<String, String> fixedTo = new HashMap<>();
		for (SolveInput.Truck t : in.trucks()) {
			for (String id : t.fixedStationIds() == null ? List.<String>of() : t.fixedStationIds()) {
				fixedTo.putIfAbsent(id, t.truckId());
			}
		}
		List<Unserved> unserved = new ArrayList<>();
		List<SolveInput.Station> open = new ArrayList<>();
		for (SolveInput.Station s : in.stations()) {
			if (blocked.contains(s.stationId())) {
				unserved.add(new Unserved(s.stationId(), s.priority(), "BLOCKED"));
			}
			else if (s.neededBikes() != 0) {
				open.add(s);
			}
		}
		open.sort(Comparator.comparing(SolveInput.Station::stationId));

		List<Truck> trucks = in.trucks().stream().map(t -> new Truck(t, in.asOf())).toList();
		while (true) {
			Truck truck = trucks.stream()
				.filter(t -> !t.finished)
				.min(Comparator.comparing((Truck t) -> t.time))
				.orElse(null);
			if (truck == null) {
				break;
			}
			SolveInput.Station best = null;
			long bestTravel = 0;
			for (SolveInput.Station s : open) {
				String owner = fixedTo.get(s.stationId());
				if (owner != null && !owner.equals(truck.in.truckId())) {
					continue;
				}
				int bikes = bikes(s, truck);
				if (bikes <= 0) {
					continue;
				}
				long travel = travelSeconds(truck.lat, truck.lon, s.lat(), s.lon());
				Instant leave = truck.time.plusSeconds(travel).plus(service(in.params(), bikes));
				Instant back = leave.plusSeconds(travelSeconds(s.lat(), s.lon(), in.depot().lat(), in.depot().lon()));
				if (back.isAfter(truck.in.availableUntil())) {
					continue;
				}
				if (best == null || travel < bestTravel || (travel == bestTravel && s.priority() > best.priority())) {
					best = s;
					bestTravel = travel;
				}
			}
			if (best == null) {
				truck.finished = true;
				continue;
			}
			int bikes = bikes(best, truck);
			boolean pickup = best.neededBikes() > 0;
			truck.load += pickup ? bikes : -bikes;
			Instant arrive = truck.time.plusSeconds(bestTravel);
			truck.stops.add(new Stop(truck.stops.size() + 1, best.stationId(), pickup ? "PICKUP" : "DROPOFF", bikes,
					truck.load, arrive, "PENDING"));
			truck.time = arrive.plus(service(in.params(), bikes));
			truck.lat = best.lat();
			truck.lon = best.lon();
			open.remove(best);
		}

		String reason = trucks.isEmpty() ? "NO_TRUCKS" : "NOT_SELECTED";
		open.forEach(s -> unserved.add(new Unserved(s.stationId(), s.priority(), reason)));
		unserved.sort(Comparator.comparingDouble(Unserved::priority).reversed().thenComparing(Unserved::stationId));
		List<Route> routes = trucks.stream()
			.map(t -> new Route(t.in.truckId(), t.in.capacity(), t.in.load(), List.copyOf(t.stops)))
			.toList();
		return new Result(routes, List.copyOf(unserved));
	}

	/** 이 트럭이 지금 이 대여소에서 옮길 수 있는 대수. 회수는 빈 자리만큼, 배치는 실은 만큼. */
	private static int bikes(SolveInput.Station s, Truck truck) {
		return s.neededBikes() > 0 ? Math.min(s.neededBikes(), truck.in.capacity() - truck.load)
				: Math.min(-s.neededBikes(), truck.load);
	}

	private static Duration service(SolveInput.Params params, int bikes) {
		return Duration.ofSeconds((long) Math.ceil((params.serviceMinutesBase() + params.serviceMinutesPerBike() * bikes) * 60));
	}

	/** 하버사인 직선거리 × 1.3을 시속 20km로. 초 올림. */
	static long travelSeconds(double lat1, double lon1, double lat2, double lon2) {
		double dLat = Math.toRadians(lat2 - lat1);
		double dLon = Math.toRadians(lon2 - lon1);
		double a = Math.pow(Math.sin(dLat / 2), 2)
				+ Math.cos(Math.toRadians(lat1)) * Math.cos(Math.toRadians(lat2)) * Math.pow(Math.sin(dLon / 2), 2);
		double km = 2 * EARTH_KM * Math.asin(Math.sqrt(a));
		return (long) Math.ceil(km * DETOUR / SPEED_KMH * 3600);
	}
}
