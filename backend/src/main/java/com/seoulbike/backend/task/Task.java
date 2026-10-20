package com.seoulbike.backend.task;

import java.time.Instant;
import java.util.UUID;

import tools.jackson.databind.JsonNode;

/** 계약 Task 스키마 그대로. pendingApproval은 APPROVAL_REQUIRED 이벤트의 approval 객체다. */
public record Task(UUID taskId, TaskType type, TaskStatus status, String shiftId, String createdBy,
		Instant createdAt, Instant updatedAt, String answer, JsonNode sources, JsonNode pendingApproval, String error,
		String traceId) {
}
