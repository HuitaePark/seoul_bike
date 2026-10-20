package com.seoulbike.backend.route;

import java.util.List;

/** 트럭 하나의 경로 변경 (계약 RouteDiff). 정류장 목록은 방문 순서대로다. */
public record RouteDiff(String truckId, List<Stop> before, List<Stop> after, double addedMinutes) {

	/** 계약 Stop 중 등급 판단에 쓰는 값. 같은 대여소·작업·대수면 같은 정류장이다. */
	public record Stop(String stationId, Action action, int bikes) {
	}

	public enum Action {
		PICKUP, DROPOFF
	}

	/** 변경이 어디서 시작됐는가. 관리자 요청에서 비롯된 변경은 언제나 큰 변경이다 (PRD 6.6). */
	public enum Origin {
		FIELD_REPORT, MANAGER_REQUEST
	}
}
