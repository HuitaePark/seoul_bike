package com.seoulbike.backend.plan;

import java.util.Optional;
import java.util.Set;
import java.util.UUID;

import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Transactional;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

/**
 * solve.results 메시지를 요청 행에 저장한다. 요청마다 결과는 하나만 받는다:
 * optimizer는 같은 결과를 두 번 보낼 수 있고(같은 eventId), 재시도 컨테이너가 같은 메시지를 다시 줄 수도 있다.
 */
@Component
class SolveResultStore {

	private static final Set<String> STATUSES = Set.of("OK", "TIMEOUT", "FAILED");

	/** 처음 저장한 결과. 저장 트랜잭션이 끝난 뒤 기다리는 요청에 넘긴다. */
	record Completed(String requestId, JsonNode result) {
	}

	/** 아직 모르는 요청의 결과. 요청은 발행 전에 저장하므로 보통은 없지만, 다시 해 볼 값은 있다 (solve.results.retry → dlq). */
	static final class UnknownSolveRequestException extends RuntimeException {
		UnknownSolveRequestException(String requestId, UUID eventId) {
			super("모르는 경로 계산 요청의 결과다: requestId=" + requestId + " eventId=" + eventId);
		}
	}

	private final SolveRequestRepository requests;
	private final JsonMapper json;

	SolveResultStore(SolveRequestRepository requests, JsonMapper json) {
		this.requests = requests;
		this.json = json;
	}

	/** 형식이 깨졌으면 IllegalArgumentException (재시도 없이 dlq). 이미 결과를 받은 요청이면 비어 있다. */
	@Transactional
	Optional<Completed> store(JsonNode message) {
		if (!message.isObject()) {
			throw new IllegalArgumentException("solve.results 메시지는 JSON 객체여야 한다");
		}
		UUID eventId = UUID.fromString(message.path("eventId").asString());
		String requestId = message.path("requestId").asString("");
		if (requestId.isBlank()) {
			throw new IllegalArgumentException("solve.results 메시지에 requestId가 없다: eventId=" + eventId);
		}
		String status = message.path("status").asString("");
		if (!STATUSES.contains(status)) {
			throw new IllegalArgumentException("모르는 결과 상태다: " + status + " (requestId=" + requestId + ")");
		}
		SolveRequestRepository.Locked request = requests.lock(requestId)
			.orElseThrow(() -> new UnknownSolveRequestException(requestId, eventId));
		if (!request.shiftId().equals(message.path("shiftId").asString(""))) {
			throw new IllegalArgumentException("결과의 shiftId가 요청과 다르다: requestId=" + requestId);
		}
		if (request.hasResult()) {
			return Optional.empty();
		}
		requests.complete(requestId, status, json.writeValueAsString(message), eventId);
		return Optional.of(new Completed(requestId, message));
	}
}
