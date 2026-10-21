package com.seoulbike.backend.station;

import java.time.Instant;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.function.Function;
import java.util.stream.Collectors;

import org.springframework.stereotype.Service;

import com.seoulbike.backend.common.ApiException;
import com.seoulbike.backend.report.FieldReportRepository;
import com.seoulbike.backend.report.FieldReportRepository.ShiftReport;
import com.seoulbike.backend.station.StationRepository.Forecast;
import com.seoulbike.backend.station.StationRepository.Observed;

/** 현황 지도(openapi)와 business-mcp 대여소·예측 도구(internal-api)가 같은 판단(StationRisk)을 쓴다. */
@Service
class StationService {

	/** 대여소 상세에는 horizonH 인자가 없다. 예측 재고·위험은 FR-01 기본값 3시간으로 본다. */
	static final int DETAIL_HORIZON_H = 3;

	static final int RECENT_REPORTS = 5;

	/** 계약 openapi StationStatus. */
	record StationStatus(String stationId, String name, double lat, double lon, int docks, int bikes,
			Instant observedAt, Integer forecastBikes, StationRisk.Kind risk, String role, Double elevationM) {
	}

	/** 계약 internal-api RiskStation (StationStatus + 필요 대수·우선순위·근거). */
	record RiskStation(String stationId, String name, double lat, double lon, int docks, int bikes, Instant observedAt,
			Integer forecastBikes, StationRisk.Kind risk, String role, Double elevationM, int neededBikes,
			double priority, Instant emptyAt, List<String> reasons) {
	}

	/** 계약 internal-api StationDetail (StationStatus + 구 · 최근 현장 보고). */
	record StationDetail(String stationId, String name, double lat, double lon, int docks, int bikes,
			Instant observedAt, Integer forecastBikes, StationRisk.Kind risk, String role, Double elevationM,
			String district, List<ShiftReport> recentReports) {
	}

	private record Missing(String detail, List<String> suggestions) {
	}

	private final StationRepository stations;
	private final FieldReportRepository reports;

	StationService(StationRepository stations, FieldReportRepository reports) {
		this.stations = stations;
		this.reports = reports;
	}

	List<StationStatus> region(String regionId, int horizonH) {
		requireRegion(regionId);
		return stations.observedInRegion(regionId, horizonH).stream().map(o -> {
			StationRisk.Assessment a = assess(o, horizonH);
			return new StationStatus(o.stationId(), o.name(), o.lat(), o.lon(), o.docks(), o.bikes(), o.observedAt(),
					a.forecastBikes(), a.risk(), o.role(), o.elevationM());
		}).toList();
	}

	/** 위험(EMPTY·OVERFLOW)인 대여소만, 우선순위 내림차순(같으면 대여소 ID 순, NFR-04). */
	List<RiskStation> risk(String regionId, int horizonH, int limit) {
		requireRegion(regionId);
		List<RiskStation> out = new ArrayList<>();
		for (Observed o : stations.observedInRegion(regionId, horizonH)) {
			StationRisk.Assessment a = assess(o, horizonH);
			if (a.risk() != StationRisk.Kind.NONE) {
				out.add(new RiskStation(o.stationId(), o.name(), o.lat(), o.lon(), o.docks(), o.bikes(),
						o.observedAt(), a.forecastBikes(), a.risk(), o.role(), o.elevationM(), a.neededBikes(),
						a.priority(), a.emptyAt(), a.reasons()));
			}
		}
		return out.stream()
			.sorted(Comparator.comparingDouble(RiskStation::priority).reversed()
				.thenComparing(RiskStation::stationId))
			.limit(limit)
			.toList();
	}

	StationDetail detail(String stationId) {
		Observed o = stations.observed(stationId, DETAIL_HORIZON_H).orElseThrow(() -> {
			if (stations.activeAmong(List.of(stationId)).isEmpty()) {
				return notFound(List.of(stationId));
			}
			return ApiException.notFound("STATION_STATUS_NOT_FOUND", "'" + stationId
					+ "' 대여소의 실시간 재고가 아직 없다. 수집기가 5분마다 넣으므로 잠시 뒤 다시 부른다.");
		});
		StationRisk.Assessment a = assess(o, DETAIL_HORIZON_H);
		return new StationDetail(o.stationId(), o.name(), o.lat(), o.lon(), o.docks(), o.bikes(), o.observedAt(),
				a.forecastBikes(), a.risk(), o.role(), o.elevationM(), o.district(),
				reports.recentAtStation(stationId, RECENT_REPORTS));
	}

	/** 요청한 순서대로. 그 start·horizonH 예측이 없는 대여소는 빠진다 (오류가 아니다). */
	List<Forecast> forecasts(List<String> stationIds, Instant start, int horizonH) {
		Set<String> ids = new LinkedHashSet<>(stationIds);
		Set<String> active = stations.activeAmong(ids);
		List<String> unknown = ids.stream().filter(id -> !active.contains(id)).toList();
		if (!unknown.isEmpty()) {
			throw notFound(unknown);
		}
		Map<String, Forecast> byStation = stations.forecasts(ids, start, horizonH).stream()
			.collect(Collectors.toMap(Forecast::stationId, Function.identity()));
		return ids.stream().map(byStation::get).filter(f -> f != null).toList();
	}

	private static StationRisk.Assessment assess(Observed o, int horizonH) {
		return StationRisk.assess(o.bikes(), o.docks(), o.observedAt(), horizonH, o.window());
	}

	private void requireRegion(String regionId) {
		List<String> regions = stations.regionIds();
		if (!regions.contains(regionId)) {
			throw ApiException.notFound("REGION_NOT_FOUND",
					"'" + regionId + "' 권역이 없다. 권역 ID는 강북 N01~N08, 강남 S01~S09다.", regions);
		}
	}

	/** 다시 부를 수 있게 대여소마다 고칠 후보를 붙인다 (internal-api StationNotFound). */
	ApiException notFound(List<String> stationIds) {
		List<Missing> missing = stationIds.stream().map(this::explain).toList();
		return ApiException.notFound("STATION_NOT_FOUND",
				missing.stream().map(Missing::detail).collect(Collectors.joining(" ")),
				missing.stream().flatMap(m -> m.suggestions().stream()).distinct().toList());
	}

	private Missing explain(String stationId) {
		var ref = stations.find(stationId);
		if (ref.isPresent()) {
			// 주간 기준 정보에서 빠진 대여소. 지난 계획이 가리킬 수 있어 행은 남아 있다 (V10).
			List<String> near = stations.nearestActive(ref.get().regionId(), ref.get().lat(), ref.get().lon(), 3);
			return new Missing("'" + stationId + "'(" + ref.get().name() + ")는 운영하지 않는 대여소다. 같은 구역의 가까운 대여소: "
					+ String.join(", ", near) + ".", near);
		}
		List<String> byNo = stations.activeByStationNo(stationId);
		if (!byNo.isEmpty()) {
			return new Missing("'" + stationId + "'는 대여소번호다. 대여소 ID(ST-숫자)는 " + String.join(", ", byNo) + "다.",
					byNo);
		}
		List<String> byName = stations.activeByNameFragment(stationId, 5);
		return new Missing("'" + stationId + "' 대여소가 없다. 대여소 ID는 실시간 API ID(ST-숫자)다"
				+ (byName.isEmpty() ? "." : ". 이름이 맞는 대여소: " + String.join(", ", byName) + "."), byName);
	}
}
