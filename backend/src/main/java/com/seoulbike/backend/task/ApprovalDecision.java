package com.seoulbike.backend.task;

import java.util.List;
import java.util.UUID;

import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Size;

/** 계약 ApprovalDecision. */
public record ApprovalDecision(@NotNull UUID approvalId, @NotNull Decision decision, List<String> excludeStationIds,
		@Size(max = 500) String comment) {

	public enum Decision {
		APPROVE, REJECT
	}
}
