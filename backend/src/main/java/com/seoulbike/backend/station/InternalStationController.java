package com.seoulbike.backend.station;

import java.time.Instant;
import java.util.List;

import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.Size;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

/**
 * business-mcp `get_risk_stations`·`get_station_detail`·`get_forecast` (internal-api). 서비스 토큰으로만 연다 (SecurityConfig).
 * {@code @Validated}를 붙이지 않는다 (붙이면 내장 파라미터 검증이 꺼져 범위 오류가 500이 된다).
 */
@RestController
class InternalStationController {

	private final StationService stations;

	InternalStationController(StationService stations) {
		this.stations = stations;
	}

	@GetMapping("/internal/stations/risk")
	List<StationService.RiskStation> risk(@RequestParam String regionId,
			@RequestParam(defaultValue = "3") @Min(1) @Max(5) int horizonH,
			@RequestParam(defaultValue = "50") @Min(1) @Max(200) int limit) {
		return stations.risk(regionId, horizonH, limit);
	}

	@GetMapping("/internal/stations/{stationId}")
	StationService.StationDetail detail(@PathVariable String stationId) {
		return stations.detail(stationId);
	}

	/** stationIds는 쉼표로 잇거나(`a,b`) 이름을 되풀이해(`stationIds=a&stationIds=b`) 보낸다. */
	@GetMapping("/internal/forecasts")
	List<StationRepository.Forecast> forecasts(@RequestParam @Size(min = 1, max = 200) List<String> stationIds,
			@RequestParam Instant start, @RequestParam(defaultValue = "3") @Min(1) @Max(5) int horizonH) {
		return stations.forecasts(stationIds, start, horizonH);
	}
}
