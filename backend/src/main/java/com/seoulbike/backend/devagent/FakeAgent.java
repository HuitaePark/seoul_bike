package com.seoulbike.backend.devagent;

import java.time.Instant;
import java.util.UUID;
import java.util.function.Consumer;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.kafka.annotation.KafkaListener;
import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.stereotype.Component;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ObjectNode;

import com.seoulbike.backend.task.Topics;

/**
 * 진짜 agent가 붙기 전까지 ai.tasks → ai.events 흐름을 흉내 낸다. 계약(asyncapi.yaml)과 같은 메시지를 보내므로
 * 화면과 SSE·승인 흐름을 지금 만들 수 있다. 진짜 agent를 띄우면 FAKE_AGENT_ENABLED=false로 끈다.
 * 모든 답변에 "가짜 에이전트"를 붙여 실제 결과로 오해하지 않게 한다. 데이터는 바꾸지 않는다.
 */
@Component
@ConditionalOnProperty(name = "app.fake-agent.enabled", havingValue = "true")
class FakeAgent {

	private static final Logger log = LoggerFactory.getLogger(FakeAgent.class);
	private static final String LABEL = "[가짜 에이전트] ";

	private final KafkaTemplate<String, String> kafka;
	private final JsonMapper json;
	private final long stepMillis;

	FakeAgent(KafkaTemplate<String, String> kafka, JsonMapper json,
			@Value("${app.fake-agent.step-millis:250}") long stepMillis) {
		this.kafka = kafka;
		this.json = json;
		this.stepMillis = stepMillis;
		log.warn("가짜 에이전트가 켜져 있다. 진짜 agent를 붙이면 FAKE_AGENT_ENABLED=false로 끈다");
	}

	@KafkaListener(topics = Topics.AI_TASKS, groupId = "fake-agent")
	void onTask(String value) {
		JsonNode cmd = json.readTree(value);
		String type = cmd.path("type").asString();
		if ("RESUME".equals(type)) {
			resume(cmd);
			return;
		}
		tool(cmd, "business_get_risk_stations", "위험 대여소 조회 중");
		tool(cmd, "rag_search_docs", "근거 문서 검색 중");
		switch (type) {
			case "PLAN_DRAFT" -> {
				tokens(cmd, "근무조 계획 초안을 만들었다. 트럭 3대로 위험 대여소 18곳을 돌고 2곳은 시간이 모자라 뺐다.");
				approval(cmd, "SUBMIT_PLAN", "MANAGER", null, null, LABEL + "근무조 계획 초안 v1 승인 요청");
			}
			case "REQUEST" -> {
				tokens(cmd, "요청을 제약 조건으로 바꿨다: " + cmd.path("text").asString(""));
				approval(cmd, "ADD_CONSTRAINT", "MANAGER", null, null, LABEL + "제약 추가 승인 요청");
			}
			case "FIELD_REPORT" -> {
				tokens(cmd, "현장 보고를 반영해 truck-1의 남은 경로를 다시 짰다. 정류장 1곳이 빠지고 6분 늘어난다.");
				approval(cmd, "ROUTE_CHANGE", "DRIVER", "MINOR", "truck-1", LABEL + "truck-1 경로 변경 수락 요청");
			}
			default -> {
				tokens(cmd, "질문에 대한 답이다: " + cmd.path("text").asString("") + " → 실제 답변은 진짜 agent가 만든다.");
				done(cmd, LABEL + "질문을 받았다. 진짜 agent가 붙으면 도구 결과와 문서 근거로 답한다.", true);
			}
		}
	}

	private void resume(JsonNode cmd) {
		boolean approved = "APPROVE".equals(cmd.path("resume").path("decision").asString());
		if (approved) {
			tokens(cmd, "승인을 받아 변경을 적용했다.");
			done(cmd, LABEL + "승인된 변경을 적용했다고 가정한다. 실제 데이터는 바꾸지 않았다.", false);
		}
		else {
			done(cmd, LABEL + "거절되어 아무것도 바꾸지 않았다.", false);
		}
	}

	private void tool(JsonNode cmd, String name, String label) {
		emit(cmd, "TOOL_START", e -> e.putObject("tool").put("name", name).put("label", label));
		emit(cmd, "TOOL_END", e -> e.putObject("tool").put("name", name).put("label", label).put("ok", true));
	}

	private void tokens(JsonNode cmd, String text) {
		for (String word : (LABEL + text).split(" ")) {
			emit(cmd, "TOKEN", e -> e.put("token", word + " "));
		}
	}

	private void approval(JsonNode cmd, String kind, String approver, String grade, String truckId, String summary) {
		emit(cmd, "APPROVAL_REQUIRED", e -> {
			ObjectNode a = e.putObject("approval");
			a.put("approvalId", UUID.randomUUID().toString());
			a.put("kind", kind);
			a.put("approver", approver);
			a.put("grade", grade);
			a.put("truckId", truckId);
			a.put("summary", summary);
			a.putArray("reasons").add("가짜 에이전트가 만든 예시 근거다").add("진짜 agent는 도구 결과로 근거를 채운다");
		});
	}

	private void done(JsonNode cmd, String answer, boolean withSource) {
		emit(cmd, "DONE", e -> {
			e.put("answer", answer);
			var sources = e.putArray("sources");
			if (withSource) {
				sources.addObject().put("kind", "DOC").put("title", "재배치 운영 기준 (예시)").put("uri", "doc://ops-guide")
					.put("excerpt", "가짜 에이전트 예시 근거");
			}
		});
	}

	private void emit(JsonNode cmd, String type, Consumer<ObjectNode> body) {
		ObjectNode e = json.createObjectNode();
		e.put("eventId", UUID.randomUUID().toString());
		e.put("schemaVersion", 1);
		e.put("occurredAt", Instant.now().toString());
		e.put("shiftId", cmd.path("shiftId").asString());
		e.put("traceId", cmd.path("traceId").asString(null));
		e.put("taskId", cmd.path("taskId").asString());
		e.put("type", type);
		body.accept(e);
		kafka.send(Topics.AI_EVENTS, cmd.path("shiftId").asString(), json.writeValueAsString(e));
		pause();
	}

	private void pause() {
		try {
			Thread.sleep(stepMillis);
		}
		catch (InterruptedException e) {
			Thread.currentThread().interrupt();
		}
	}
}
