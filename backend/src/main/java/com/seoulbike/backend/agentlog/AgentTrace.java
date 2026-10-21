package com.seoulbike.backend.agentlog;

import java.time.Instant;
import java.util.UUID;

import tools.jackson.databind.JsonNode;

/**
 * 계약 openapi AgentTrace: 에이전트 추론 기록 한 줄. id는 받은 순서(SSE id)이고, detail은 agent가 마스킹한 JSON 그대로다.
 * 관리자 로그에만 쓴다. 작업 진행 이벤트(TaskEvent)와 섞지 않는다.
 */
public record AgentTrace(long id, UUID eventId, UUID taskId, String shiftId, String traceId, String runId, String source,
		String kind, Integer turn, String title, JsonNode detail, Instant occurredAt, Instant receivedAt) {
}
