package com.seoulbike.backend.agentlog;

import java.util.UUID;

import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.Size;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;

import com.seoulbike.backend.common.ApiException;

/**
 * 에이전트 추론 기록 관리자 로그 (계약 openapi v0.16 agent-log). /api/admin/** 는 SecurityConfig에서 MANAGER만 연다.
 * 읽기만 한다. 작업·승인·업무 데이터는 건드리지 않는다.
 * {@code @Validated}를 붙이지 않는다. 붙이면 Spring 7 내장 파라미터 검증이 꺼져 범위 오류가 500이 된다.
 */
@RestController
class AgentTraceController {

	private final AgentTraceStore store;
	private final AgentTraceHub hub;

	AgentTraceController(AgentTraceStore store, AgentTraceHub hub) {
		this.store = store;
		this.hub = hub;
	}

	@GetMapping("/api/admin/agent-traces")
	AgentTraceStore.Page list(@RequestParam(required = false) UUID taskId,
			@RequestParam(required = false) @Size(max = 100) String shiftId,
			@RequestParam(required = false) @Min(1) Long before, @RequestParam(required = false) @Min(0) Long after,
			@RequestParam(defaultValue = "200") @Min(1) @Max(AgentTraceStore.MAX_LIMIT) int limit) {
		if (before != null && after != null) {
			throw new ApiException(HttpStatus.BAD_REQUEST, "INVALID_REQUEST", "before와 after는 함께 쓰지 않는다");
		}
		AgentTraceStore.Filter filter = new AgentTraceStore.Filter(taskId, blankToNull(shiftId));
		if (after != null) {
			return store.after(filter, after, limit);
		}
		return before != null ? store.before(filter, before, limit) : store.latest(filter, limit);
	}

	@GetMapping(path = "/api/admin/agent-traces/stream", produces = MediaType.TEXT_EVENT_STREAM_VALUE)
	SseEmitter stream(@RequestParam(required = false) UUID taskId,
			@RequestParam(required = false) @Size(max = 100) String shiftId,
			@RequestHeader(value = "Last-Event-ID", required = false) String lastEventId) {
		return hub.subscribe(new AgentTraceStore.Filter(taskId, blankToNull(shiftId)), parseId(lastEventId));
	}

	private static String blankToNull(String value) {
		return value == null || value.isBlank() ? null : value.trim();
	}

	private static Long parseId(String lastEventId) {
		if (lastEventId == null || lastEventId.isBlank()) {
			return null;
		}
		try {
			return Math.max(0, Long.parseLong(lastEventId.trim()));
		}
		catch (NumberFormatException e) {
			return null;
		}
	}
}
