package com.seoulbike.backend.route;

import java.util.List;
import java.util.Set;
import java.util.stream.Collectors;

import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.stereotype.Component;

/**
 * 변경 등급 규칙 (PRD 6.6, FR-07). 기준값은 설정(app.route-change.*)으로 바꾼다.
 * <ul>
 * <li>NONE: 어느 트럭도 정류장 순서·대수가 바뀌지 않음
 * <li>MINOR: 트럭 한 대 안의 변경, 빠지는 정류장 2곳 이하, 소요 시간 증가 10분 이하
 * <li>MAJOR: 그 밖의 모든 변경 (트럭 간 이동, 트럭 추가 투입, 여러 트럭 변경, 기준 초과, 관리자 요청)
 * </ul>
 */
@Component
@EnableConfigurationProperties(RouteChangeGrader.Limits.class)
public class RouteChangeGrader {

	/** 기준값은 application.properties의 app.route-change.* 에 있다. 환경 변수로도 바꿀 수 있다. */
	@ConfigurationProperties("app.route-change")
	record Limits(int minorMaxDroppedStops, int minorMaxAddedMinutes) {
	}

	private final Limits limits;

	RouteChangeGrader(Limits limits) {
		this.limits = limits;
	}

	public RouteChangeGrade grade(List<RouteDiff> diffs, RouteDiff.Origin origin) {
		List<RouteDiff> changed = diffs.stream().filter(d -> !d.before().equals(d.after())).toList();
		if (changed.isEmpty()) {
			return RouteChangeGrade.NONE;
		}
		// 트럭 간 이동은 두 트럭이 함께 바뀌므로 여기서 걸린다.
		if (origin == RouteDiff.Origin.MANAGER_REQUEST || changed.size() > 1) {
			return RouteChangeGrade.MAJOR;
		}
		RouteDiff diff = changed.getFirst();
		if (diff.before().isEmpty()) {
			return RouteChangeGrade.MAJOR; // 경로가 없던 트럭을 새로 투입
		}
		Set<String> remaining = diff.after().stream().map(RouteDiff.Stop::stationId).collect(Collectors.toSet());
		long dropped = diff.before().stream().map(RouteDiff.Stop::stationId).distinct()
			.filter(s -> !remaining.contains(s))
			.count();
		boolean small = dropped <= limits.minorMaxDroppedStops() && diff.addedMinutes() <= limits.minorMaxAddedMinutes();
		return small ? RouteChangeGrade.MINOR : RouteChangeGrade.MAJOR;
	}
}
