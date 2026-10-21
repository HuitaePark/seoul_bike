package com.seoulbike.backend.plan;

import java.time.Instant;
import java.util.List;

import com.fasterxml.jackson.annotation.JsonInclude;

/**
 * 계약 asyncapi SolveRequest 중 봉투 밖의 필드. 공용 JsonMapper로 바꿔 보내므로 시각은 +09:00으로 나간다.
 * 시간 초과 임시안(GreedyPlanner)도 같은 입력으로 계산한다.
 */
record SolveInput(Point depot, Instant asOf, List<Station> stations, List<Truck> trucks,
		List<String> blockedStationIds, Params params) {

	record Point(double lat, double lon) {
	}

	/** @param kind EMPTY(배치, neededBikes 음수) 또는 OVERFLOW(회수, 양수) */
	record Station(String stationId, double lat, double lon, int neededBikes, double priority, String kind) {
	}

	/** @param fixedStationIds REPLAN에서 이 트럭에만 맡길 대여소. 없으면 빼고 보낸다 */
	record Truck(String truckId, int capacity, int load, double lat, double lon, Instant availableUntil,
			@JsonInclude(JsonInclude.Include.NON_EMPTY) List<String> fixedStationIds) {
	}

	record Params(int solutionLimit, double serviceMinutesBase, double serviceMinutesPerBike) {
	}
}
