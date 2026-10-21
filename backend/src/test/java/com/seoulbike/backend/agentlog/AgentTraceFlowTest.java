package com.seoulbike.backend.agentlog;

import static org.assertj.core.api.Assertions.assertThat;

import java.io.BufferedReader;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.time.Instant;
import java.util.ArrayList;
import java.util.List;
import java.util.UUID;
import java.util.concurrent.TimeUnit;
import java.util.function.Predicate;

import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.web.server.LocalServerPort;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.kafka.core.KafkaTemplate;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ObjectNode;

import com.seoulbike.backend.IntegrationTest;
import com.seoulbike.backend.task.Topics;

/**
 * 에이전트 추론 기록(관리자 로그): ai.traces → agent_trace_events → /api/admin/agent-traces(·/stream).
 * 관리자만 보고, 깨진 메시지는 건너뛰며, 작업 진행 이벤트·작업 상태는 건드리지 않는다 (asyncapi v0.6, openapi v0.16).
 */
@IntegrationTest
class AgentTraceFlowTest {

	private static final Duration WAIT = Duration.ofSeconds(30);

	@LocalServerPort
	int port;

	@Autowired
	JsonMapper json;

	@Autowired
	KafkaTemplate<String, String> kafka;

	@Autowired
	JdbcClient jdbc;

	@Autowired
	AgentTraceStore store;

	private final HttpClient http = HttpClient.newHttpClient();

	@Test
	void managerReadsTheReasoningLogInOrderAndNobodyElseCan() throws Exception {
		UUID taskId = UUID.randomUUID();
		String shiftId = "N02:2026-10-22:AM";
		send(trace(taskId, shiftId, "RUN_START", "모델 경로 시작 · field_report · 도구 10개 중에서 모델이 고른다", null));
		send(trace(taskId, shiftId, "DECISION", "선택: business_request_route_plan(kind=REPLAN)", 1)
			.set("detail", json.readTree("""
					{"calls":[{"name":"business_request_route_plan","args":{"kind":"REPLAN"},
					"rationale":"진입 불가 보고라 TR-03만 다시 계산한다"}],"model":"gpt-4.1-mini"}""")));
		send(trace(taskId, shiftId, "TOOL_RESULT", "← business_request_route_plan 성공 0.40초, 1,541자", 1));

		String manager = token("manager");
		JsonNode page = awaitItems(manager, "taskId=" + taskId, 3);
		JsonNode items = page.path("items");
		assertThat(fields(items, "kind")).containsExactly("RUN_START", "DECISION", "TOOL_RESULT");
		assertThat(items.get(0).path("id").asLong()).isLessThan(items.get(1).path("id").asLong());
		assertThat(items.get(1).path("detail").path("calls").get(0).path("rationale").asString())
			.isEqualTo("진입 불가 보고라 TR-03만 다시 계산한다");
		assertThat(items.get(1).path("turn").asInt()).isEqualTo(1);
		assertThat(items.get(0).path("turn").isNull()).isTrue();
		assertThat(items.get(0).path("occurredAt").asString()).endsWith("+09:00");
		assertThat(page.path("hasMore").asBoolean()).isFalse();

		String path = "/api/admin/agent-traces?taskId=" + taskId;
		assertThat(call("GET", path, token("driver1")).statusCode()).isEqualTo(403);
		assertThat(call("GET", path, token("executive")).statusCode()).isEqualTo(403);
		assertThat(call("GET", path, null).statusCode()).isEqualTo(401);
		assertThat(call("GET", "/api/admin/agent-traces/stream", token("driver1")).statusCode()).isEqualTo(403);
	}

	@Test
	void brokenOrRepeatedTracesAreSkippedAndTheLogKeepsGoing() throws Exception {
		UUID taskId = UUID.randomUUID();
		ObjectNode first = trace(taskId, "s-trace", "ROUTE", "QUESTION → 모델 경로", null);
		send(first);
		kafka.send(Topics.AI_TRACES, "s-trace", "not-json-" + taskId).get(10, TimeUnit.SECONDS);
		send(trace(taskId, "s-trace", "", "kind가 없다", null));
		send(first); // 같은 eventId (재전송)
		send(trace(taskId, "s-trace", "RUN_END", "완료 · 1턴, 도구 0회, 0.20초", null));

		JsonNode items = awaitItems(token("manager"), "taskId=" + taskId, 2).path("items");
		assertThat(fields(items, "kind")).containsExactly("ROUTE", "RUN_END");
	}

	@Test
	void tracesNeverBecomeTaskEventsOrChangeATask() throws Exception {
		String manager = token("manager");
		var created = call("POST", "/api/ai/tasks", manager,
				"{\"type\":\"QUESTION\",\"shiftId\":\"N02:2026-10-22:PM\",\"text\":\"회수가 왜 많지?\"}");
		assertThat(created.statusCode()).isEqualTo(202);
		UUID taskId = UUID.fromString(json.readTree(created.body()).path("taskId").asString());
		// 가짜 에이전트(TestInfra)가 작업을 끝낼 때까지 기다린 뒤 그 작업의 추론 기록을 보낸다
		long deadline = System.nanoTime() + WAIT.toNanos();
		while (!"DONE".equals(taskStatus(taskId)) && System.nanoTime() < deadline) {
			Thread.sleep(100);
		}
		assertThat(taskStatus(taskId)).isEqualTo("DONE");
		int eventsBefore = taskEvents(taskId);

		send(trace(taskId, "N02:2026-10-22:PM", "RUN_START", "모델 경로 시작 · qa", null));
		send(trace(taskId, "N02:2026-10-22:PM", "APPROVAL_WAIT", "승인 대기 (MANAGER 1/1): 계획 P-1 활성화", 2));
		awaitItems(manager, "taskId=" + taskId, 2);

		assertThat(taskEvents(taskId)).isEqualTo(eventsBefore);
		assertThat(taskStatus(taskId)).isEqualTo("DONE");
		assertThat(jdbc.sql("select count(*) from approvals where task_id = :taskId").param("taskId", taskId)
			.query(Long.class).single()).isZero();
	}

	@Test
	void streamFollowsFromLastEventIdAndOnlyTheChosenTask() throws Exception {
		UUID taskId = UUID.randomUUID();
		UUID other = UUID.randomUUID();
		send(trace(taskId, "s-stream", "RUN_START", "시작", null));
		long firstId = awaitItems(token("manager"), "taskId=" + taskId, 1).path("items").get(0).path("id").asLong();

		var req = HttpRequest.newBuilder(uri("/api/admin/agent-traces/stream?taskId=" + taskId)).timeout(WAIT)
			.header("Authorization", "Bearer " + token("manager"))
			.header("Accept", "text/event-stream")
			.header("Last-Event-ID", String.valueOf(firstId))
			.build();
		HttpResponse<InputStream> res = http.send(req, HttpResponse.BodyHandlers.ofInputStream());
		assertThat(res.statusCode()).isEqualTo(200);
		send(trace(other, "s-stream", "DECISION", "다른 작업", 1));
		send(trace(taskId, "s-stream", "DECISION", "선택: business_get_station_detail(station_id=ST-1660)", 1));
		send(trace(taskId, "s-stream", "RUN_END", "완료", null));

		try (var reader = new BufferedReader(new InputStreamReader(res.body(), StandardCharsets.UTF_8))) {
			List<String[]> events = readEvents(reader, e -> e[1].equals("RUN_END"));
			assertThat(events).extracting(e -> e[1]).containsExactly("DECISION", "RUN_END");
			assertThat(Long.parseLong(events.get(0)[0])).isGreaterThan(firstId);
			JsonNode data = json.readTree(events.get(0)[2]);
			assertThat(data.path("taskId").asString()).isEqualTo(taskId.toString());
			assertThat(data.path("title").asString()).contains("ST-1660");
		}
	}

	@Test
	void pagesGoBackAndForwardAndBadQueriesAre400() throws Exception {
		UUID taskId = UUID.randomUUID();
		for (int i = 1; i <= 5; i++) {
			store.insert(trace(taskId, "s-page", "TOOL_CALL", "호출 " + i, i));
		}
		String manager = token("manager");
		JsonNode latest = get(manager, "taskId=" + taskId + "&limit=2");
		assertThat(fields(latest.path("items"), "title")).containsExactly("호출 4", "호출 5");
		assertThat(latest.path("hasMore").asBoolean()).isTrue();

		long oldestShown = latest.path("items").get(0).path("id").asLong();
		JsonNode older = get(manager, "taskId=" + taskId + "&limit=2&before=" + oldestShown);
		assertThat(fields(older.path("items"), "title")).containsExactly("호출 2", "호출 3");
		assertThat(older.path("hasMore").asBoolean()).isTrue();

		long first = older.path("items").get(0).path("id").asLong();
		JsonNode newer = get(manager, "taskId=" + taskId + "&limit=10&after=" + first);
		assertThat(fields(newer.path("items"), "title")).containsExactly("호출 3", "호출 4", "호출 5");
		assertThat(newer.path("hasMore").asBoolean()).isFalse();

		assertThat(call("GET", "/api/admin/agent-traces?before=5&after=1", manager).statusCode()).isEqualTo(400);
		assertThat(call("GET", "/api/admin/agent-traces?limit=501", manager).statusCode()).isEqualTo(400);
		assertThat(call("GET", "/api/admin/agent-traces?taskId=not-a-uuid", manager).statusCode()).isEqualTo(400);
	}

	@Test
	void purgeDropsOnlyTracesPastRetention() {
		UUID taskId = UUID.randomUUID();
		store.insert(trace(taskId, "s-purge", "RUN_START", "오래된 기록", null));
		store.insert(trace(taskId, "s-purge", "RUN_END", "최근 기록", null));
		jdbc.sql("update agent_trace_events set received_at = now() - interval '30 days' where task_id = :taskId and kind = 'RUN_START'")
			.param("taskId", taskId)
			.update();

		store.purge();

		assertThat(store.latest(new AgentTraceStore.Filter(taskId, null), 10).items())
			.extracting(AgentTrace::title).containsExactly("최근 기록");
	}

	private ObjectNode trace(UUID taskId, String shiftId, String kind, String title, Integer turn) {
		ObjectNode node = json.createObjectNode()
			.put("eventId", UUID.randomUUID().toString())
			.put("schemaVersion", 1)
			.put("occurredAt", Instant.now().toString())
			.put("shiftId", shiftId)
			.put("traceId", "trace-" + taskId)
			.put("taskId", taskId.toString())
			.put("source", "HARNESS")
			.put("kind", kind)
			.put("title", title);
		if (turn == null) {
			node.putNull("turn");
		}
		else {
			node.put("turn", turn);
		}
		node.putObject("detail");
		return node;
	}

	private void send(ObjectNode trace) throws Exception {
		kafka.send(Topics.AI_TRACES, trace.path("shiftId").asString(), json.writeValueAsString(trace)).get(10, TimeUnit.SECONDS);
	}

	private JsonNode awaitItems(String token, String query, int count) throws Exception {
		long deadline = System.nanoTime() + WAIT.toNanos();
		JsonNode page = null;
		while (System.nanoTime() < deadline) {
			page = get(token, query);
			if (page.path("items").size() >= count) {
				return page;
			}
			Thread.sleep(100);
		}
		throw new AssertionError("추론 기록 " + count + "건을 기다렸다. 마지막 응답: " + page);
	}

	private JsonNode get(String token, String query) throws Exception {
		var res = call("GET", "/api/admin/agent-traces?" + query, token);
		assertThat(res.statusCode()).isEqualTo(200);
		return json.readTree(res.body());
	}

	/** 목록 항목마다 맨 위 필드 하나 (detail 속 같은 이름은 보지 않는다). */
	private static List<String> fields(JsonNode items, String field) {
		List<String> values = new ArrayList<>();
		items.forEach(item -> values.add(item.path(field).asString()));
		return values;
	}

	private String taskStatus(UUID taskId) {
		return jdbc.sql("select status from ai_tasks where task_id = :taskId").param("taskId", taskId).query(String.class)
			.single();
	}

	private int taskEvents(UUID taskId) {
		return jdbc.sql("select count(*) from ai_task_events where task_id = :taskId").param("taskId", taskId)
			.query(Integer.class).single();
	}

	/** id·event·data 묶음을 last가 참이 될 때까지 읽는다. 주석(: ping)은 건너뛴다. */
	private static List<String[]> readEvents(BufferedReader reader, Predicate<String[]> last) throws Exception {
		List<String[]> events = new ArrayList<>();
		String id = null;
		String event = null;
		StringBuilder data = new StringBuilder();
		long deadline = System.nanoTime() + WAIT.toNanos();
		String line;
		while (System.nanoTime() < deadline && (line = reader.readLine()) != null) {
			if (line.isEmpty()) {
				if (event != null) {
					String[] e = { id, event, data.toString() };
					events.add(e);
					if (last.test(e)) {
						return events;
					}
				}
				id = null;
				event = null;
				data.setLength(0);
			}
			else if (line.startsWith("id:")) {
				id = line.substring(3).trim();
			}
			else if (line.startsWith("event:")) {
				event = line.substring(6).trim();
			}
			else if (line.startsWith("data:")) {
				data.append(line.substring(5).trim());
			}
		}
		throw new AssertionError("기다린 이벤트가 오지 않았다. 받은 이벤트: " + events.size());
	}

	private String token(String loginId) throws Exception {
		var res = call("POST", "/api/auth/login", null, "{\"loginId\":\"" + loginId + "\",\"password\":\"test-password\"}");
		assertThat(res.statusCode()).isEqualTo(200);
		return json.readTree(res.body()).path("accessToken").asString();
	}

	private HttpResponse<String> call(String method, String path, String token) throws Exception {
		return call(method, path, token, null);
	}

	private HttpResponse<String> call(String method, String path, String token, String body) throws Exception {
		var req = HttpRequest.newBuilder(uri(path)).timeout(WAIT)
			.method(method, body == null ? HttpRequest.BodyPublishers.noBody() : HttpRequest.BodyPublishers.ofString(body));
		if (body != null) {
			req.header("Content-Type", "application/json");
		}
		if (token != null) {
			req.header("Authorization", "Bearer " + token);
		}
		return http.send(req.build(), HttpResponse.BodyHandlers.ofString());
	}

	private URI uri(String path) {
		return URI.create("http://localhost:" + port + path);
	}
}
