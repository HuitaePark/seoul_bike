package com.seoulbike.backend.devagent;

import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;
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
import tools.jackson.databind.node.ArrayNode;
import tools.jackson.databind.node.ObjectNode;

import com.seoulbike.backend.auth.Role;
import com.seoulbike.backend.common.ApiException;
import com.seoulbike.backend.common.SeoulTime;
import com.seoulbike.backend.route.RouteChangeGrade;
import com.seoulbike.backend.route.RouteChangeGrader;
import com.seoulbike.backend.route.RouteDiff;
import com.seoulbike.backend.route.RouteDiff.Action;
import com.seoulbike.backend.route.RouteDiff.Stop;
import com.seoulbike.backend.task.ChangeApprovals;
import com.seoulbike.backend.task.Topics;

/**
 * 진짜 agent가 붙기 전까지 ai.tasks → ai.events 흐름을 흉내 낸다. 계약(asyncapi.yaml)과 같은 메시지를 보내므로
 * 화면과 SSE·승인 흐름을 지금 만들 수 있다. 진짜 agent를 띄우면 FAKE_AGENT_ENABLED=false로 끈다.
 * 모든 답변에 "가짜 에이전트"를 붙여 실제 결과로 오해하지 않게 한다. 데이터는 바꾸지 않는다.
 *
 * <p>경로 변경은 진짜 흐름과 같은 순서를 보여 준다: backend 등급 규칙으로 승인 순서를 정하고, 카드를 순서대로 보내고,
 * 마지막 승인 뒤에는 적용 API(/internal/routes/{truckId}/apply, M3)가 쓸 확인 규칙을 불러 통과할 때만 적용했다고 한다.
 */
@Component
@ConditionalOnProperty(name = "app.fake-agent.enabled", havingValue = "true")
class FakeAgent {

	private static final Logger log = LoggerFactory.getLogger(FakeAgent.class);
	private static final String LABEL = "[가짜 에이전트] ";
	private static final String TRUCK = "TR-01";
	private static final List<Stop> ROUTE = List.of(new Stop("ST-101", Action.PICKUP, 5),
			new Stop("ST-102", Action.PICKUP, 3), new Stop("ST-103", Action.DROPOFF, 6), new Stop("ST-104", Action.DROPOFF, 2));

	/** 현장 보고: ST-102 진입 불가로 한 곳이 빠지고 4분 늘어난다 → 작은 변경. 트럭은 보고한 기사의 트럭이다. */
	private static final List<Stop> FIELD_REPORT_AFTER = List.of(ROUTE.get(0), new Stop("ST-103", Action.DROPOFF, 3),
			ROUTE.get(3));

	/** 관리자 요청: 행사장 근처 ST-201에 4대를 더 놓는다 → 관리자 요청에서 비롯된 큰 변경. */
	private static final RouteDiff REQUEST_DIFF = new RouteDiff(TRUCK, ROUTE, List.of(ROUTE.get(0), ROUTE.get(1),
			new Stop("ST-201", Action.DROPOFF, 4), new Stop("ST-103", Action.DROPOFF, 2), ROUTE.get(3)), 7);

	private record Candidate(String candidateId, RouteDiff diff, RouteChangeGrade grade) {
	}

	private final KafkaTemplate<String, String> kafka;
	private final JsonMapper json;
	private final RouteChangeGrader grader;
	private final ChangeApprovals changeApprovals;
	private final long stepMillis;
	/** 승인을 기다리는 카드 → 승인되면 이어서 할 일. 재시작하면 잊는다 (가짜라서 괜찮다). */
	private final Map<String, Consumer<JsonNode>> onApprove = new ConcurrentHashMap<>();

	FakeAgent(KafkaTemplate<String, String> kafka, JsonMapper json, RouteChangeGrader grader,
			ChangeApprovals changeApprovals, @Value("${app.fake-agent.step-millis:250}") long stepMillis) {
		this.kafka = kafka;
		this.json = json;
		this.grader = grader;
		this.changeApprovals = changeApprovals;
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
				approval(cmd, "SUBMIT_PLAN", Role.MANAGER, null, LABEL + "근무조 계획 초안 v1 승인 요청", a -> {
				}, c -> done(c, LABEL + "승인된 계획을 활성화했다고 가정한다.", false));
			}
			case "REQUEST" -> {
				tokens(cmd, "요청을 제약 조건으로 바꿨다: " + cmd.path("text").asString(""));
				approval(cmd, "ADD_CONSTRAINT", Role.MANAGER, null, LABEL + "제약 추가 승인 요청", a -> {
				}, c -> {
					tokens(c, "제약을 반영해 " + TRUCK + " 경로를 다시 짰다. 관리자 요청에서 비롯된 변경이라 관리자 승인 뒤 기사 수락이 필요하다.");
					proposeRoute(c, REQUEST_DIFF, RouteDiff.Origin.MANAGER_REQUEST);
				});
			}
			case "FIELD_REPORT" -> {
				// 보고한 기사의 트럭 경로를 바꾼다. 명령에 보고 값(report)이 같이 온다 (계약 v0.4).
				String truck = cmd.path("requestedBy").path("truckId").asString(TRUCK);
				JsonNode report = cmd.path("report");
				tokens(cmd, "현장 보고(" + report.path("stationId").asString("?") + " " + report.path("issue").asString("")
						+ ")를 반영해 " + truck + "의 남은 경로를 다시 짰다. 정류장 1곳이 빠지고 4분 늘어난다.");
				proposeRoute(cmd, new RouteDiff(truck, ROUTE, FIELD_REPORT_AFTER, 4), RouteDiff.Origin.FIELD_REPORT);
			}
			default -> {
				tokens(cmd, "질문에 대한 답이다: " + cmd.path("text").asString("") + " → 실제 답변은 진짜 agent가 만든다.");
				done(cmd, LABEL + "질문을 받았다. 진짜 agent가 붙으면 도구 결과와 문서 근거로 답한다.", true);
			}
		}
	}

	private void resume(JsonNode cmd) {
		JsonNode resume = cmd.path("resume");
		Consumer<JsonNode> next = onApprove.remove(resume.path("approvalId").asString());
		if (!"APPROVE".equals(resume.path("decision").asString())) {
			done(cmd, LABEL + "거절되어 아무것도 바꾸지 않았다. 기존 경로를 유지한다.", false);
		}
		else if (next == null) {
			done(cmd, LABEL + "승인된 변경을 적용했다고 가정한다 (재시작으로 진행 상태를 잊었다).", false);
		}
		else {
			next.accept(cmd);
		}
	}

	/** backend 등급 규칙으로 승인 순서를 정하고 첫 카드를 보낸다. 에이전트가 등급으로 승인자를 판단하지 않는다. */
	private void proposeRoute(JsonNode cmd, RouteDiff diff, RouteDiff.Origin origin) {
		RouteChangeGrade grade = grader.grade(List.of(diff), origin);
		if (grade == RouteChangeGrade.NONE) {
			done(cmd, LABEL + "경로가 그대로라 바꿀 것이 없다.", false);
			return;
		}
		routeCard(cmd, new Candidate("cand-" + UUID.randomUUID(), diff, grade), 0);
	}

	private void routeCard(JsonNode cmd, Candidate candidate, int step) {
		List<Role> approvers = candidate.grade().requiredApprovers();
		Role approver = approvers.get(step);
		String truckId = candidate.diff().truckId();
		String summary = approver == Role.MANAGER ? truckId + " 경로 변경 승인 요청 (큰 변경, 승인 뒤 기사 수락)"
				: truckId + " 경로 변경 수락 요청";
		approval(cmd, "ROUTE_CHANGE", approver, truckId, LABEL + summary, a -> routeFields(a, candidate),
				c -> {
					if (step + 1 < approvers.size()) {
						routeCard(c, candidate, step + 1);
					}
					else {
						applyRoute(c, candidate);
					}
				});
	}

	/** 적용 API가 할 확인을 똑같이 거친다. 승인 순서가 모자라면 적용하지 않고 실패로 끝낸다. */
	private void applyRoute(JsonNode cmd, Candidate candidate) {
		UUID taskId = UUID.fromString(cmd.path("taskId").asString());
		UUID lastApprovalId = UUID.fromString(cmd.path("resume").path("approvalId").asString());
		String truckId = candidate.diff().truckId();
		try {
			changeApprovals.requireRouteChange(taskId, lastApprovalId, truckId, candidate.candidateId(), candidate.grade());
		}
		catch (ApiException e) {
			emit(cmd, "FAILED", f -> f.put("error", LABEL + "경로를 적용하지 않았다: " + e.getMessage()).put("code", e.code()));
			return;
		}
		tokens(cmd, "승인 순서(" + candidate.grade().requiredApprovers() + ")를 확인했다.");
		done(cmd, LABEL + truckId + " 경로 변경을 적용했다고 가정한다. 실제 경로는 바꾸지 않았다.", false);
	}

	private void routeFields(ObjectNode a, Candidate candidate) {
		a.put("grade", candidate.grade().name());
		a.put("candidateId", candidate.candidateId());
		ArrayNode order = a.putArray("requiredApprovers");
		candidate.grade().requiredApprovers().forEach(r -> order.add(r.name()));
		ObjectNode diff = a.putObject("diff");
		diff.put("truckId", candidate.diff().truckId());
		stops(diff.putArray("before"), candidate.diff().before());
		stops(diff.putArray("after"), candidate.diff().after());
		diff.put("addedMinutes", candidate.diff().addedMinutes());
	}

	private static void stops(ArrayNode out, List<Stop> stops) {
		for (int i = 0; i < stops.size(); i++) {
			Stop s = stops.get(i);
			out.addObject().put("seq", i + 1).put("stationId", s.stationId()).put("action", s.action().name())
				.put("bikes", s.bikes());
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

	private void approval(JsonNode cmd, String kind, Role approver, String truckId, String summary,
			Consumer<ObjectNode> fields, Consumer<JsonNode> next) {
		String approvalId = UUID.randomUUID().toString();
		onApprove.put(approvalId, next);
		emit(cmd, "APPROVAL_REQUIRED", e -> {
			ObjectNode a = e.putObject("approval");
			a.put("approvalId", approvalId);
			a.put("kind", kind);
			a.put("approver", approver.name());
			a.put("truckId", truckId);
			a.put("summary", summary);
			a.putArray("reasons").add("가짜 에이전트가 만든 예시 근거다").add("진짜 agent는 도구 결과로 근거를 채운다");
			fields.accept(a);
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
		e.put("occurredAt", SeoulTime.now());
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
