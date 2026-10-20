package com.seoulbike.backend.task;

import java.time.Instant;
import java.util.List;
import java.util.Optional;
import java.util.Set;
import java.util.UUID;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Transactional;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ObjectNode;

/**
 * ai.events 메시지를 저장하고 작업 상태에 반영한다.
 * seq는 여기서 작업마다 1부터 매긴다 (SSE id). 같은 eventId는 한 번만 저장한다 (재전송·리밸런스 대비).
 */
@Component
class TaskEventStore {

	private static final Logger log = LoggerFactory.getLogger(TaskEventStore.class);

	/**
	 * 계약 TaskEvent 필드(seq 제외). 이것만 저장하고 SSE로 보낸다. 봉투(eventId 등)와 agent 확장 필드(harness의
	 * runId·토큰 수 등)는 화면, 특히 기사 브라우저로 가지 않게 버린다.
	 */
	private static final Set<String> EVENT_FIELDS = Set.of("taskId", "type", "occurredAt", "token", "tool", "approval",
			"answer", "sources", "error", "code");

	record StoredEvent(UUID taskId, int seq, String type, String data) {

		boolean terminal() {
			return "DONE".equals(type) || "FAILED".equals(type);
		}
	}

	private final JdbcClient jdbc;
	private final JsonMapper json;
	private final TaskRepository tasks;
	private final ApprovalRepository approvals;

	TaskEventStore(JdbcClient jdbc, JsonMapper json, TaskRepository tasks, ApprovalRepository approvals) {
		this.jdbc = jdbc;
		this.json = json;
		this.tasks = tasks;
		this.approvals = approvals;
	}

	/** 아직 모르는 작업의 이벤트. 작업 커밋보다 먼저 왔을 수 있어 다시 해 볼 값이 있다 (ai.events.retry → dlq). */
	static final class UnknownTaskException extends RuntimeException {
		UnknownTaskException(UUID taskId, UUID eventId) {
			super("모르는 작업의 이벤트다: taskId=" + taskId + " eventId=" + eventId);
		}
	}

	@Transactional
	Optional<StoredEvent> store(JsonNode message) {
		if (!message.isObject()) {
			throw new IllegalArgumentException("ai.events 메시지는 JSON 객체여야 한다");
		}
		UUID eventId = UUID.fromString(message.path("eventId").asString());
		UUID taskId = UUID.fromString(message.path("taskId").asString());
		String type = message.path("type").asString();
		if (tasks.find(taskId).isEmpty()) {
			throw new UnknownTaskException(taskId, eventId);
		}

		ObjectNode payload = ((ObjectNode) message).deepCopy();
		payload.retain(EVENT_FIELDS);
		if (!payload.has("occurredAt")) {
			payload.put("occurredAt", Instant.now().toString());
		}

		Optional<Integer> seq = jdbc.sql("""
				insert into ai_task_events (task_id, seq, event_id, type, payload, occurred_at)
				select :taskId, coalesce(max(seq), 0) + 1, :eventId, :type, cast(:payload as jsonb), cast(:occurredAt as timestamptz)
				from ai_task_events where task_id = :taskId
				on conflict (event_id) do nothing
				returning seq
				""")
			.param("taskId", taskId)
			.param("eventId", eventId)
			.param("type", type)
			.param("payload", json.writeValueAsString(payload))
			.param("occurredAt", payload.path("occurredAt").asString())
			.query(Integer.class)
			.optional();
		if (seq.isEmpty()) {
			return Optional.empty();
		}

		apply(taskId, type, payload);
		payload.put("seq", seq.get());
		return Optional.of(new StoredEvent(taskId, seq.get(), type, json.writeValueAsString(payload)));
	}

	List<StoredEvent> after(UUID taskId, int seq) {
		return jdbc.sql("""
				select seq, type, payload::text as payload from ai_task_events
				where task_id = :taskId and seq > :seq order by seq
				""")
			.param("taskId", taskId)
			.param("seq", seq)
			.query((rs, i) -> {
				ObjectNode payload = (ObjectNode) json.readTree(rs.getString("payload"));
				payload.put("seq", rs.getInt("seq"));
				return new StoredEvent(taskId, rs.getInt("seq"), rs.getString("type"), json.writeValueAsString(payload));
			})
			.list();
	}

	private void apply(UUID taskId, String type, ObjectNode payload) {
		switch (type) {
			case "TOKEN", "TOOL_START", "TOOL_END" -> tasks.markRunning(taskId);
			case "APPROVAL_REQUIRED" -> {
				JsonNode approval = payload.path("approval");
				UUID approvalId = UUID.fromString(approval.path("approvalId").asString());
				approvals.supersedePending(taskId);
				approvals.insertPending(new ApprovalRepository.NewApproval(approvalId, taskId,
						approval.path("kind").asString(), approval.path("approver").asString(),
						approval.path("truckId").asString(null), approval.path("candidateId").asString(null),
						approval.path("grade").asString(null), json.writeValueAsString(approval)));
				tasks.waitForApproval(taskId, approvalId);
			}
			case "DONE" -> {
				approvals.supersedePending(taskId);
				tasks.finish(taskId, payload.path("answer").asString(null),
						json.writeValueAsString(payload.has("sources") ? payload.get("sources") : json.createArrayNode()));
			}
			case "FAILED" -> {
				approvals.supersedePending(taskId);
				tasks.fail(taskId, payload.path("error").asString("에이전트 처리 실패"));
			}
			default -> log.warn("모르는 이벤트 종류를 저장만 한다: {}", type);
		}
	}
}
