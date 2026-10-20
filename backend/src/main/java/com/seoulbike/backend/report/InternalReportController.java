package com.seoulbike.backend.report;

import java.time.Instant;
import java.util.List;

import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

/** business-mcp `get_field_reports` (internal-api). 서비스 토큰으로만 연다 (SecurityConfig). */
@RestController
class InternalReportController {

	private final FieldReportRepository reports;

	InternalReportController(FieldReportRepository reports) {
		this.reports = reports;
	}

	@GetMapping("/internal/shifts/{shiftId}/reports")
	List<FieldReportRepository.ShiftReport> reports(@PathVariable String shiftId,
			@RequestParam(required = false) Instant since) {
		return reports.forShift(shiftId, since);
	}
}
