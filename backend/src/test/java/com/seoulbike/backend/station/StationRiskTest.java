package com.seoulbike.backend.station;

import static org.assertj.core.api.Assertions.assertThat;

import java.time.Duration;
import java.time.Instant;

import org.junit.jupiter.api.Test;

import com.seoulbike.backend.station.StationRisk.Assessment;
import com.seoulbike.backend.station.StationRisk.Kind;
import com.seoulbike.backend.station.StationRisk.Window;

/** 위험 판단의 경계값. 화면 색과 MCP 우선순위가 이 값으로 정해진다. */
class StationRiskTest {

	private static final Instant OBSERVED = Instant.parse("2026-10-21T22:00:00Z");
	private static final Instant START = Instant.parse("2026-10-21T22:00:00Z");

	@Test
	void 예측이_없으면_현재_재고로만_판단하고_2대_이하가_빈_위험이다() {
		Assessment three = StationRisk.assess(3, 10, OBSERVED, 3, null);
		assertThat(three.risk()).isEqualTo(Kind.NONE);
		assertThat(three.forecastBikes()).isNull();
		assertThat(three.neededBikes()).isZero();
		assertThat(three.priority()).isZero();
		assertThat(three.reasons()).contains("예측 없음: 현재 재고로만 판단");

		Assessment two = StationRisk.assess(2, 10, OBSERVED, 3, null);
		assertThat(two.risk()).isEqualTo(Kind.EMPTY);
		assertThat(two.neededBikes()).isEqualTo(-1);
		assertThat(two.priority()).isEqualTo(2.0);
		assertThat(two.emptyAt()).isNull();

		// 이미 0대면 예상 빈 시각은 관측 시각이다.
		Assessment zero = StationRisk.assess(0, 10, OBSERVED, 3, null);
		assertThat(zero.neededBikes()).isEqualTo(-3);
		assertThat(zero.priority()).isEqualTo(6.0);
		assertThat(zero.emptyAt()).isEqualTo(OBSERVED);
	}

	@Test
	void 거치대_수를_넘어야_과적재이고_빈_위험이_먼저다() {
		assertThat(StationRisk.assess(10, 10, OBSERVED, 3, null).risk()).isEqualTo(Kind.NONE);
		Assessment over = StationRisk.assess(11, 10, OBSERVED, 3, null);
		assertThat(over.risk()).isEqualTo(Kind.OVERFLOW);
		assertThat(over.neededBikes()).isEqualTo(1);
		assertThat(over.priority()).isEqualTo(1.0);
		// 거치대가 2개 이하인 대여소에서 재고 2대는 둘 다 해당하지만 빈 위험으로 본다.
		assertThat(StationRisk.assess(2, 1, OBSERVED, 3, null).risk()).isEqualTo(Kind.EMPTY);
	}

	@Test
	void 구간_합계로_끝_재고를_보고_멀수록_우선순위가_낮다() {
		Assessment a = StationRisk.assess(8, 10, OBSERVED, 3, new Window(START, 6.4, 0.5, null, null));
		// 8 + 0.5 − 6.4 = 2.1 → 2대
		assertThat(a.forecastBikes()).isEqualTo(2);
		assertThat(a.risk()).isEqualTo(Kind.EMPTY);
		assertThat(a.neededBikes()).isEqualTo(-1);
		assertThat(a.priority()).isEqualTo(0.5); // 1대 × 2 / (1 + 3시간)
		assertThat(a.reasons()).contains("3시간 예측: 대여 6.4 · 반납 0.5 → 2대", "빈 위험: 3시간 뒤 2대 (기준 2대 이하)");

		// 반올림해 3대면 위험이 아니다.
		assertThat(StationRisk.assess(8, 10, OBSERVED, 3, new Window(START, 5.4, 0, null, null)).risk())
			.isEqualTo(Kind.NONE);
	}

	@Test
	void 시간별_값이_있으면_구간_중간에_비는_것도_잡는다() {
		double[] rentals = { 4, 0, 0 };
		double[] returns = { 0, 2, 2 };
		Assessment hourly = StationRisk.assess(5, 10, OBSERVED, 3, new Window(START, 4, 4, rentals, returns));
		assertThat(hourly.forecastBikes()).isEqualTo(5);
		assertThat(hourly.risk()).isEqualTo(Kind.EMPTY);
		assertThat(hourly.neededBikes()).isEqualTo(-2); // 1시간 뒤 1대 → 3대까지
		assertThat(hourly.priority()).isEqualTo(2.0); // 2대 × 2 / (1 + 1시간)
		// 합계만 있으면 끝 재고가 그대로라 놓친다.
		assertThat(StationRisk.assess(5, 10, OBSERVED, 3, new Window(START, 4, 4, null, null)).risk())
			.isEqualTo(Kind.NONE);
	}

	@Test
	void 예상_재고는_0_밑으로_가지_않고_처음_0대가_되는_시각이_예상_빈_시각이다() {
		Assessment a = StationRisk.assess(1, 10, OBSERVED, 3, new Window(START, 5, 0, null, null));
		assertThat(a.forecastBikes()).isZero();
		assertThat(a.neededBikes()).isEqualTo(-7); // 궤적 최저 −4 → 3대까지
		assertThat(a.emptyAt()).isEqualTo(START.plus(Duration.ofHours(3)));
		// 지금 이미 2대 이하라 임박도는 1이다.
		assertThat(a.priority()).isEqualTo(14.0);
	}

	@Test
	void 과적재는_궤적_최고점이_거치대_수가_되게_회수한다() {
		Assessment a = StationRisk.assess(9, 10, OBSERVED, 2,
				new Window(START, 1, 5, new double[] { 0, 1 }, new double[] { 4, 1 }));
		assertThat(a.risk()).isEqualTo(Kind.OVERFLOW);
		assertThat(a.neededBikes()).isEqualTo(3); // 1시간 뒤 13대
		assertThat(a.priority()).isEqualTo(1.5);
		assertThat(a.reasons()).contains("과적재: 1시간 뒤 13대로 거치대 10개를 넘음");
	}
}
