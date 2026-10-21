package com.seoulbike.backend.station;

import java.util.List;

import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.Min;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

/**
 * 현황 지도 (FR-01). 로그인한 모든 역할이 본다 (openapi 기본 bearerAuth). 대여소 재고는 개인 정보도, 다른 트럭 경로도 아니다.
 * {@code @Validated}를 붙이지 않는다 (TaskController 참고: 붙이면 내장 파라미터 검증이 꺼진다).
 */
@RestController
class StationController {

	private final StationService stations;

	StationController(StationService stations) {
		this.stations = stations;
	}

	@GetMapping("/api/regions/{regionId}/stations")
	List<StationService.StationStatus> region(@PathVariable String regionId,
			@RequestParam(defaultValue = "3") @Min(1) @Max(5) int horizonH) {
		return stations.region(regionId, horizonH);
	}
}
