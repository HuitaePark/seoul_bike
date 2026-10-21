package com.seoulbike.backend.station;

import java.util.ArrayList;
import java.util.Collection;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

import org.springframework.stereotype.Component;

/**
 * 경로 계산 입력(asyncapi SolveStation)에 쓸 대여소별 필요 대수·우선순위. 위험 판단은 위험 대여소 API와 같은 StationRisk다
 * (운영 중이고 관측이 있는 대여소, 실시간 수집에 현장 보고를 반영한 재고(StationRepository), 끝나지 않은 가장 이른 예측 구간).
 * 제약 두 가지는 여기서 반영한다. 재고와 거치대 수, 유형 가중치가 이 패키지에 있어서다.
 */
@Component
public class StationNeeds {

	/**
	 * 대여소 하나. 위험이 없으면 neededBikes·priority가 0이다.
	 *
	 * @param neededBikes 양수 회수, 음수 배치 (계약 RiskStation.neededBikes와 같은 뜻)
	 */
	public record Need(String stationId, double lat, double lon, int neededBikes, double priority) {
	}

	private final StationRepository stations;
	private final StationService service;

	StationNeeds(StationRepository stations, StationService service) {
		this.stations = stations;
		this.service = service;
	}

	/**
	 * 구역의 관측이 있는 운영 대여소 전부 (대여소 ID 순).
	 *
	 * @param inventory INVENTORY_OVERRIDE: 대여소 → 현장에서 센 재고. 관측 재고 대신 이 값으로 위험을 판단한다
	 * @param targetAdjust TARGET_ADJUST: 대여소 → 목표 재고 가감 대수. 목표를 v대 올리면 v대 더 배치(neededBikes − v)한다.
	 *        바꾼 뒤에도 배치는 지금 빈 거치대 수를, 회수는 지금 재고를 넘지 않는다. 바꾼 대여소의 우선순위는
	 *        |neededBikes| × 유형 가중치(배치 2, 회수 1)로 다시 매긴다 (관리자가 지금 정한 목표라 임박도 1)
	 */
	public List<Need> region(String regionId, int horizonH, Map<String, Integer> inventory,
			Map<String, Integer> targetAdjust) {
		List<Need> out = new ArrayList<>();
		for (StationRepository.Observed o : stations.observedInRegion(regionId, horizonH)) {
			int bikes = inventory.getOrDefault(o.stationId(), o.bikes());
			StationRisk.Assessment a = StationRisk.assess(bikes, o.docks(), o.observedAt(), horizonH, o.window());
			int needed = a.neededBikes();
			double priority = a.priority();
			Integer adjust = targetAdjust.get(o.stationId());
			if (adjust != null) {
				needed = Math.clamp((long) needed - adjust, -Math.max(0, o.docks() - bikes), bikes);
				double weight = needed < 0 ? StationRisk.EMPTY_WEIGHT : StationRisk.OVERFLOW_WEIGHT;
				priority = Math.round(Math.abs(needed) * weight * 100) / 100.0;
			}
			out.add(new Need(o.stationId(), o.lat(), o.lon(), needed, priority));
		}
		return out;
	}

	/** 운영하지 않거나 없는 대여소가 있으면 404 STATION_NOT_FOUND (고칠 후보를 붙인다, internal-api StationNotFound). */
	public void requireActive(Collection<String> stationIds) {
		Set<String> ids = new LinkedHashSet<>(stationIds);
		if (ids.isEmpty()) {
			return;
		}
		Set<String> active = stations.activeAmong(ids);
		List<String> unknown = ids.stream().filter(id -> !active.contains(id)).toList();
		if (!unknown.isEmpty()) {
			throw service.notFound(unknown);
		}
	}
}
