package com.seoulbike.backend.report;

import jakarta.validation.Valid;
import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Size;
import org.springframework.http.HttpStatus;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.ResponseStatus;
import org.springframework.web.bind.annotation.RestController;

import com.seoulbike.backend.auth.CurrentUser;
import com.seoulbike.backend.report.FieldReportRepository.Issue;

/** 현장 보고. 기사(DRIVER)만 부를 수 있다 (SecurityConfig). */
@RestController
class FieldReportController {

	record FieldReportRequest(@NotBlank @Size(max = 100) String shiftId, @NotBlank @Size(max = 64) String stationId,
			@NotNull @Min(0) Integer actualBikes, @Min(0) Integer loaded, @Min(0) Integer unloaded, Issue issue,
			@Size(max = 500) String memo) {
	}

	private final FieldReportService reports;

	FieldReportController(FieldReportService reports) {
		this.reports = reports;
	}

	@PostMapping("/api/reports")
	@ResponseStatus(HttpStatus.ACCEPTED)
	FieldReportService.Accepted report(@AuthenticationPrincipal Jwt jwt, @Valid @RequestBody FieldReportRequest req) {
		return reports.report(CurrentUser.from(jwt), req);
	}
}
