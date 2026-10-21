package com.seoulbike.backend.station;

import java.time.Duration;
import java.time.Instant;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;

/**
 * 대여소 하나의 위험 판단 (FR-01, PRD 7.1). 숫자는 모두 여기서 계산한다 (NFR-07). 계약 문구: openapi `RiskKind`·`StationStatus`,
 * internal-api `RiskStation`.
 *
 * <p>재고 궤적: 관측 시각의 재고에서 시작해 예측 구간 가운데 **관측 시각 뒤에 남은 부분**의 순유입(반납 − 대여)만 더한다.
 * 구간이 이미 진행 중이면 지난 순유입은 관측 재고에 이미 들어 있어 다시 더하면 두 번 센다. 시간별 값(rentalsH·returnsH)이 있으면
 * 매시 경계(구간 시작 + k시간)마다 점을 만들고, 관측 시각이 걸친 시간은 남은 비율만큼만 더한다 (합의 15: 합계만 보면 구간 중간에
 * 비는 대여소를 놓친다). 시간별 값이 없으면 합계가 구간에 고르게 퍼진다고 보고 남은 비율만큼 더한 끝 점 하나를 만든다.
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
	 * @param neededBikes 양수 회수, 음수 배치. 빈 위험은 궤적 최저점이 3대가 되게, 과적재는 최고점이 거치대 수가 되게.
	 *        배치는 지금 빈 거치대 수(거치대 − 재고)를, 회수는 지금 재고를 넘지 않는다
	 * @param emptyAt 궤적이 처음 0대 이하가 되는 시각 (지금 0대면 관측 시각). 그런 점이 없으면 null
	 */
	record Assessment(Integer forecastBikes, Kind risk, int neededBikes, double priority, Instant emptyAt,
			List<String> reasons) {
	}

	/** @param hours 관측 시각부터 이 점까지의 시간 (지금이면 0) */
	private record Point(double hours, Instant at, long bikes) {
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
					+ " → 구간 끝 " + forecastBikes + "대");
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
			// 지금 빈 거치대보다 많이 내려놓을 수는 없다.
			needed = (int) Math.max(min - (EMPTY_AT_MOST + 1), -Math.max(0, docks - bikes));
			weight = EMPTY_WEIGHT;
			first = firstEmpty;
			reasons.add("빈 위험: " + when(first) + " " + first.bikes() + "대 (기준 " + EMPTY_AT_MOST + "대 이하)");
		}
		else if (firstOver != null) {
			kind = Kind.OVERFLOW;
			long max = points.stream().mapToLong(Point::bikes).max().orElseThrow();
			// 지금 있는 자전거보다 많이 실을 수는 없다.
			needed = (int) Math.min(max - docks, bikes);
			weight = OVERFLOW_WEIGHT;
			first = firstOver;
			reasons.add("과적재: " + when(first) + " " + first.bikes() + "대로 거치대 " + docks + "개를 넘음");
		}
		else {
			return new Assessment(forecastBikes, Kind.NONE, 0, 0, emptyAt, List.copyOf(reasons));
		}
		// 우선순위 = 옮길 대수 × 유형 가중치 × 임박도. 임박도는 관측 시각부터 위험에 처음 닿기까지의 시간 k(지금이면 0)로 1/(1+k).
		// PRD 7.1-4의 수요 강도는 아직 넣지 않는다: 예측 합계가 이미 궤적(부족 대수)에 들어 있다.
		double priority = Math.round(Math.abs(needed) * weight / (1 + first.hours()) * 100) / 100.0;
		return new Assessment(forecastBikes, kind, needed, priority, emptyAt, List.copyOf(reasons));
	}

	private static List<Point> trajectory(int bikes, Instant observedAt, int horizonH, Window w) {
		List<Point> points = new ArrayList<>();
		points.add(new Point(0, observedAt, bikes));
		if (w == null) {
			return points;
		}
		// 구간 시작부터 관측 시각까지 지난 시간. 구간이 아직 시작 전이면 0.
		double elapsed = Math.max(0, hoursBetween(w.start(), observedAt));
		if (w.rentalsH() != null && w.returnsH() != null && w.rentalsH().length == horizonH
				&& w.returnsH().length == horizonH) {
			double stock = bikes;
			for (int k = 1; k <= horizonH; k++) {
				double remaining = Math.clamp(k - elapsed, 0.0, 1.0);
				if (remaining == 0) {
					continue;
				}
				stock += remaining * (w.returnsH()[k - 1] - w.rentalsH()[k - 1]);
				points.add(point(observedAt, w.start().plus(Duration.ofHours(k)), stock));
			}
		}
		else if (elapsed < horizonH) {
			double remaining = (horizonH - elapsed) / horizonH;
			points.add(point(observedAt, w.start().plus(Duration.ofHours(horizonH)),
					bikes + remaining * (w.returns() - w.rentals())));
		}
		return points;
	}

	private static Point point(Instant observedAt, Instant at, double stock) {
		return new Point(hoursBetween(observedAt, at), at, Math.round(stock));
	}

	private static double hoursBetween(Instant from, Instant to) {
		return Duration.between(from, to).toSeconds() / 3600.0;
	}

	/** 관측 시각 기준. 진행 중인 구간이면 다음 경계까지 1시간이 안 될 수 있어 분으로 적는다. */
	private static String when(Point p) {
		long minutes = Math.round(p.hours() * 60);
		if (minutes == 0) {
			return "지금";
		}
		if (minutes % 60 == 0) {
			return minutes / 60 + "시간 뒤";
		}
		return minutes < 60 ? minutes + "분 뒤" : minutes / 60 + "시간 " + minutes % 60 + "분 뒤";
	}

	private static String number(double v) {
		return v == Math.rint(v) ? Long.toString((long) v) : String.format(Locale.ROOT, "%.1f", v);
	}
}
