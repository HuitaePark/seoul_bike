package com.seoulbike.backend.station;

import java.time.Duration;
import java.time.Instant;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;

/**
 * 대여소 하나의 위험 판단 (FR-01, PRD 7.1). 숫자는 모두 여기서 계산한다 (NFR-07). 계약 문구: openapi `StationStatus`,
 * internal-api `RiskStation`.
 *
 * <p>재고 궤적: 현재 재고에서 시작해 예측 구간의 순유입(반납 − 대여)을 더한다. 시간별 값(rentalsH·returnsH)이 있으면 매시
 * 점을, 없으면 구간 끝 점 하나를 만든다 (합의 15: 합계만 보면 구간 중간에 비는 대여소를 놓친다). 현재 재고를 구간 시작
 * 재고로 본다. 예측은 근무조 시작 30분 전에 들어오므로 계획을 짜는 시점에는 둘이 거의 같다.
 */
final class StationRisk {

	/** 빈 위험: 재고 2대 이하 (합의 27). */
	static final int EMPTY_AT_MOST = 2;

	/** 빈 대여소가 과적재보다 급하다 (PRD 7.1-4: 대여를 못 하는 불편이 반납 혼잡보다 직접적이다). */
	static final double EMPTY_WEIGHT = 2.0;
	static final double OVERFLOW_WEIGHT = 1.0;

	enum Kind {
		EMPTY, OVERFLOW, NONE
	}

	/**
	 * 고른 예측 구간 하나. rentalsH·returnsH는 둘 다 있을 때만 시간별 궤적을 만든다.
	 *
	 * @param start 예측 구간 시작 시각
	 */
	record Window(Instant start, double rentals, double returns, double[] rentalsH, double[] returnsH) {
	}

	/**
	 * @param forecastBikes 구간 끝 예상 재고(0 밑으로 내려가지 않는다). 예측이 없으면 null
	 * @param neededBikes 양수 회수, 음수 배치. 빈 위험은 궤적 최저점이 3대가 되게, 과적재는 최고점이 거치대 수가 되게
	 * @param emptyAt 궤적이 처음 0대 이하가 되는 시각 (지금 0대면 관측 시각). 그런 점이 없으면 null
	 */
	record Assessment(Integer forecastBikes, Kind risk, int neededBikes, double priority, Instant emptyAt,
			List<String> reasons) {
	}

	private record Point(int hour, Instant at, long bikes) {
	}

	private StationRisk() {
	}

	static Assessment assess(int bikes, int docks, Instant observedAt, int horizonH, Window window) {
		List<Point> points = trajectory(bikes, observedAt, horizonH, window);
		List<String> reasons = new ArrayList<>();
		reasons.add("현재 재고 " + bikes + "대, 거치대 " + docks + "개");
		Integer forecastBikes = null;
		if (window == null) {
			reasons.add("예측 없음: 현재 재고로만 판단");
		}
		else {
			forecastBikes = (int) Math.max(0, points.getLast().bikes());
			reasons.add(horizonH + "시간 예측: 대여 " + number(window.rentals()) + " · 반납 " + number(window.returns())
					+ " → " + forecastBikes + "대");
		}

		Point firstEmpty = points.stream().filter(p -> p.bikes() <= EMPTY_AT_MOST).findFirst().orElse(null);
		Point firstOver = points.stream().filter(p -> p.bikes() > docks).findFirst().orElse(null);
		Instant emptyAt = points.stream().filter(p -> p.bikes() <= 0).map(Point::at).findFirst().orElse(null);

		Kind kind;
		int needed;
		double weight;
		Point first;
		if (firstEmpty != null) {
			kind = Kind.EMPTY;
			long min = points.stream().mapToLong(Point::bikes).min().orElseThrow();
			needed = (int) (min - (EMPTY_AT_MOST + 1));
			weight = EMPTY_WEIGHT;
			first = firstEmpty;
			reasons.add("빈 위험: " + when(first) + " " + first.bikes() + "대 (기준 " + EMPTY_AT_MOST + "대 이하)");
		}
		else if (firstOver != null) {
			kind = Kind.OVERFLOW;
			long max = points.stream().mapToLong(Point::bikes).max().orElseThrow();
			needed = (int) (max - docks);
			weight = OVERFLOW_WEIGHT;
			first = firstOver;
			reasons.add("과적재: " + when(first) + " " + first.bikes() + "대로 거치대 " + docks + "개를 넘음");
		}
		else {
			return new Assessment(forecastBikes, Kind.NONE, 0, 0, emptyAt, List.copyOf(reasons));
		}
		// 우선순위 = 부족 대수 × 유형 가중치 × 임박도. 임박도는 위험에 처음 닿는 시간 k(지금이면 0)로 1/(1+k).
		// PRD 7.1-4의 수요 강도는 아직 넣지 않는다: 예측 합계가 이미 궤적(부족 대수)에 들어 있다.
		double priority = Math.round(Math.abs(needed) * weight / (1 + first.hour()) * 100) / 100.0;
		return new Assessment(forecastBikes, kind, needed, priority, emptyAt, List.copyOf(reasons));
	}

	private static List<Point> trajectory(int bikes, Instant observedAt, int horizonH, Window w) {
		List<Point> points = new ArrayList<>();
		points.add(new Point(0, observedAt, bikes));
		if (w == null) {
			return points;
		}
		if (w.rentalsH() != null && w.returnsH() != null && w.rentalsH().length == horizonH
				&& w.returnsH().length == horizonH) {
			double stock = bikes;
			for (int k = 1; k <= horizonH; k++) {
				stock += w.returnsH()[k - 1] - w.rentalsH()[k - 1];
				points.add(new Point(k, w.start().plus(Duration.ofHours(k)), Math.round(stock)));
			}
		}
		else {
			points.add(new Point(horizonH, w.start().plus(Duration.ofHours(horizonH)),
					Math.round(bikes + w.returns() - w.rentals())));
		}
		return points;
	}

	private static String when(Point p) {
		return p.hour() == 0 ? "지금" : p.hour() + "시간 뒤";
	}

	private static String number(double v) {
		return v == Math.rint(v) ? Long.toString((long) v) : String.format(Locale.ROOT, "%.1f", v);
	}
}
