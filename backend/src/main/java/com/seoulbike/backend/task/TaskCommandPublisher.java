package com.seoulbike.backend.task;

import java.time.Instant;
import java.util.List;
import java.util.UUID;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.TimeoutException;

import org.springframework.http.HttpStatus;
import org.springframework.kafka.KafkaException;
import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.stereotype.Component;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ObjectNode;

import com.seoulbike.backend.auth.CurrentUser;
import com.seoulbike.backend.common.ApiException;

/**
 * ai.tasks로 TaskCommand를 보낸다 (계약 asyncapi.yaml). 근무조 ID가 키라 한 근무조의 명령은 순서대로 간다.
 * LangGraph thread_id는 작업 단위({shiftId}:{taskId})다. 같은 근무조 작업끼리 대기 중인 승인이 섞이지 않게 한다.
 */
@Component
class TaskCommandPublisher {

	private static final long SEND_TIMEOUT_SECONDS = 5;

	private final KafkaTemplate<String, String> kafka;
	private final JsonMapper json;

	TaskCommandPublisher(KafkaTemplate<String, String> kafka, JsonMapper json) {
		this.kafka = kafka;
		this.json = json;
	}

	/** @param report FIELD_REPORT일 때 계약 FieldReport. 명령의 reportId·report가 된다. 아니면 null */
	void newTask(UUID taskId, TaskType type, String shiftId, String text, String traceId, CurrentUser by,
			ObjectNode report) {
		ObjectNode cmd = envelope(taskId, shiftId, traceId, type.name(), by);
		if (text != null) {
			cmd.put("text", text);
		}
		if (report != null) {
			cmd.put("reportId", report.path("reportId").asString());
			cmd.set("report", report);
		}
		send(shiftId, cmd);
	}

	void resume(UUID taskId, String shiftId, String traceId, UUID approvalId, ApprovalDecision decision,
			CurrentUser by) {
		ObjectNode cmd = envelope(taskId, shiftId, traceId, "RESUME", by);
		ObjectNode resume = cmd.putObject("resume");
		resume.put("approvalId", approvalId.toString());
		resume.put("decision", decision.decision().name());
		List<String> exclude = decision.excludeStationIds() == null ? List.of() : decision.excludeStationIds();
		exclude.forEach(resume.putArray("excludeStationIds")::add);
		if (decision.comment() != null) {
			resume.put("comment", decision.comment());
		}
		ObjectNode decidedBy = resume.putObject("decidedBy");
		decidedBy.put("userId", by.id());
		decidedBy.put("role", by.role().name());
		send(shiftId, cmd);
	}

	private ObjectNode envelope(UUID taskId, String shiftId, String traceId, String type, CurrentUser by) {
		ObjectNode cmd = json.createObjectNode();
		cmd.put("eventId", UUID.randomUUID().toString());
		cmd.put("schemaVersion", 1);
		cmd.put("occurredAt", Instant.now().toString());
		cmd.put("shiftId", shiftId);
		cmd.put("traceId", traceId);
		cmd.put("taskId", taskId.toString());
		cmd.put("threadId", shiftId + ":" + taskId);
		cmd.put("type", type);
		ObjectNode requestedBy = cmd.putObject("requestedBy");
		requestedBy.put("userId", by.id());
		requestedBy.put("role", by.role().name());
		requestedBy.put("truckId", by.truckId());
		return cmd;
	}

	/** 브로커가 받았다고 확인한 뒤에야 202를 돌려준다. 실패하면 호출한 트랜잭션이 롤백된다. */
	private void send(String shiftId, ObjectNode cmd) {
		try {
			kafka.send(Topics.AI_TASKS, shiftId, json.writeValueAsString(cmd)).get(SEND_TIMEOUT_SECONDS, TimeUnit.SECONDS);
		}
		catch (InterruptedException e) {
			Thread.currentThread().interrupt();
			throw unavailable();
		}
		catch (ExecutionException | TimeoutException | KafkaException e) {
			// KafkaException: 브로커가 내려가 있으면 send가 그 자리에서 던진다. 500 대신 503으로 알린다.
			throw unavailable();
		}
	}

	private static ApiException unavailable() {
		return new ApiException(HttpStatus.SERVICE_UNAVAILABLE, "QUEUE_UNAVAILABLE", "작업 큐에 넣지 못했다. 잠시 뒤 다시 시도한다");
	}
}
