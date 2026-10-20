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

	/** Kafka 봉투 필드. 화면으로 보내는 SSE data에서는 뺀다. */
	private static final Set<String> ENVELOPE = Set.of("eventId", "schemaVersion", "shiftId", "traceId");

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

	@Transactional
	Optional<StoredEvent> store(JsonNode message) {
		UUID eventId = UUID.fromString(message.path("eventId").asString());
		UUID taskId = UUID.fromString(message.path("taskId").asString());
		String type = message.path("type").asString();
		if (tasks.find(taskId).isEmpty()) {
			log.warn("모르는 작업의 이벤트를 버린다: taskId={} eventId={}", taskId, eventId);
			return Optional.empty();
		}

		ObjectNode payload = ((ObjectNode) message).deepCopy();
		payload.remove(ENVELOPE);
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
				approvals.insertPending(approvalId, taskId, approval.path("kind").asString(),
						approval.path("approver").asString(), approval.path("truckId").asString(null),
						json.writeValueAsString(approval));
				tasks.waitForApproval(taskId, approvalId);
			}
			case "DONE" -> tasks.finish(taskId, payload.path("answer").asString(null),
					json.writeValueAsString(payload.has("sources") ? payload.get("sources") : json.createArrayNode()));
			case "FAILED" -> tasks.fail(taskId, payload.path("error").asString("에이전트 처리 실패"));
			default -> log.warn("모르는 이벤트 종류를 저장만 한다: {}", type);
		}
	}
}
