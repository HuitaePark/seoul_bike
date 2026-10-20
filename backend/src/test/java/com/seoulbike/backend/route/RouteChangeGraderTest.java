package com.seoulbike.backend.route;

import static org.assertj.core.api.Assertions.assertThat;

import java.util.List;

import org.junit.jupiter.api.Test;

import com.seoulbike.backend.auth.Role;
import com.seoulbike.backend.route.RouteDiff.Action;
import com.seoulbike.backend.route.RouteDiff.Origin;
import com.seoulbike.backend.route.RouteDiff.Stop;

/** PRD 6.6 변경 등급 규칙 (초기값: 빠지는 정류장 2곳, 시간 증가 10분). */
class RouteChangeGraderTest {

	private final RouteChangeGrader grader = new RouteChangeGrader(new RouteChangeGrader.Limits(2, 10));

	private static final Stop A = new Stop("ST-A", Action.PICKUP, 5);
	private static final Stop B = new Stop("ST-B", Action.PICKUP, 3);
	private static final Stop C = new Stop("ST-C", Action.DROPOFF, 4);
	private static final Stop D = new Stop("ST-D", Action.DROPOFF, 4);

	@Test
	void unchangedRoutesNeedNoApproval() {
		var grade = grader.grade(List.of(new RouteDiff("truck-1", List.of(A, B, C), List.of(A, B, C), 0)), Origin.FIELD_REPORT);
		assertThat(grade).isEqualTo(RouteChangeGrade.NONE);
		assertThat(grade.requiredApprovers()).isEmpty();
	}

	@Test
	void changedBikeCountIsAChange() {
		var after = List.of(A, new Stop("ST-B", Action.PICKUP, 4), C);
		assertThat(grader.grade(List.of(new RouteDiff("truck-1", List.of(A, B, C), after, 0)), Origin.FIELD_REPORT))
			.isEqualTo(RouteChangeGrade.MINOR);
	}

	@Test
	void smallChangeAtTheLimitsIsTheDriversToAccept() {
		// 2곳이 빠지고 정확히 10분 늘어나면 아직 작은 변경이다.
		var grade = grader.grade(List.of(new RouteDiff("truck-1", List.of(A, B, C, D), List.of(A, D), 10)),
				Origin.FIELD_REPORT);
		assertThat(grade).isEqualTo(RouteChangeGrade.MINOR);
		assertThat(grade.requiredApprovers()).containsExactly(Role.DRIVER);
	}

	@Test
	void exceedingEitherLimitNeedsManagerThenDriver() {
		var threeDropped = new RouteDiff("truck-1", List.of(A, B, C, D), List.of(A), 0);
		var elevenMinutes = new RouteDiff("truck-1", List.of(A, B, C), List.of(A, C), 11);
		assertThat(grader.grade(List.of(threeDropped), Origin.FIELD_REPORT)).isEqualTo(RouteChangeGrade.MAJOR);
		assertThat(grader.grade(List.of(elevenMinutes), Origin.FIELD_REPORT)).isEqualTo(RouteChangeGrade.MAJOR);
		assertThat(RouteChangeGrade.MAJOR.requiredApprovers()).containsExactly(Role.MANAGER, Role.DRIVER);
	}

	@Test
	void movingAStopToAnotherTruckIsMajor() {
		var truck1 = new RouteDiff("truck-1", List.of(A, B), List.of(A), -5);
		var truck2 = new RouteDiff("truck-2", List.of(C), List.of(B, C), 6);
		assertThat(grader.grade(List.of(truck1, truck2), Origin.FIELD_REPORT)).isEqualTo(RouteChangeGrade.MAJOR);
	}

	@Test
	void sendingOutAnIdleTruckIsMajor() {
		var idle = new RouteDiff("truck-3", List.of(), List.of(C), 8);
		assertThat(grader.grade(List.of(idle), Origin.FIELD_REPORT)).isEqualTo(RouteChangeGrade.MAJOR);
	}

	@Test
	void changesFromManagerRequestsAreAlwaysMajor() {
		var tiny = new RouteDiff("truck-1", List.of(A, B, C), List.of(A, C), 1);
		assertThat(grader.grade(List.of(tiny), Origin.MANAGER_REQUEST)).isEqualTo(RouteChangeGrade.MAJOR);
	}

	@Test
	void limitsComeFromSettings() {
		var strict = new RouteChangeGrader(new RouteChangeGrader.Limits(0, 10));
		var oneDropped = new RouteDiff("truck-1", List.of(A, B, C), List.of(A, C), 1);
		assertThat(strict.grade(List.of(oneDropped), Origin.FIELD_REPORT)).isEqualTo(RouteChangeGrade.MAJOR);
	}
}
