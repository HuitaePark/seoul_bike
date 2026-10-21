package com.seoulbike.backend.constraint;

import java.time.OffsetDateTime;
import java.util.List;
import java.util.UUID;

import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotEmpty;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Size;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.ResponseStatus;
import org.springframework.web.bind.annotation.RestController;

/** business-mcp `add_constraint` (internal-api POST /internal/constraints). 서비스 토큰으로만 연다. */
@RestController
class ConstraintController {

	enum ConstraintType {
		TARGET_ADJUST, STATION_SUSPENDED, STATION_BLOCKED, INVENTORY_OVERRIDE, PRIORITY_BOOST
	}

	/** 계약 ConstraintRequest = ApprovedChange(approvalId, taskId) + 제약 내용. */
	record ConstraintRequest(@NotNull UUID approvalId, @NotNull UUID taskId, @NotBlank @Size(max = 100) String shiftId,
			@NotNull ConstraintType type, @NotEmpty List<@NotBlank @Size(max = 64) String> stationIds, Integer value,
			@NotNull OffsetDateTime validFrom, @NotNull OffsetDateTime validTo, @NotBlank @Size(max = 500) String reason) {
	}

	/** 계약 Constraint = ConstraintRequest + constraintId. */
	record Constraint(String constraintId, UUID approvalId, UUID taskId, String shiftId, ConstraintType type,
			List<String> stationIds, Integer value, OffsetDateTime validFrom, OffsetDateTime validTo, String reason) {
	}

	private final ConstraintService constraints;

	ConstraintController(ConstraintService constraints) {
		this.constraints = constraints;
	}

	@PostMapping("/internal/constraints")
	@ResponseStatus(HttpStatus.CREATED)
	Constraint add(@Valid @RequestBody ConstraintRequest req) {
		return constraints.add(req);
	}
}
