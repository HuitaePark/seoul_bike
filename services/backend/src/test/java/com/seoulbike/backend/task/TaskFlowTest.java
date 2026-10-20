package com.seoulbike.backend.task;

import static org.assertj.core.api.Assertions.assertThat;

import java.io.UncheckedIOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import java.util.ArrayList;
import java.util.Iterator;
import java.util.List;
import java.util.UUID;
import java.util.concurrent.TimeUnit;
import java.util.function.Predicate;
import java.util.stream.Stream;

import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.web.server.LocalServerPort;
import org.springframework.context.annotation.Import;
import org.springframework.kafka.core.KafkaTemplate;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

import com.seoulbike.backend.TestInfra;

/**
 * M2 완료 기준: 로그인 → 작업 202 → SSE로 진행 이벤트 → 승인 → 같은 스트림에서 DONE.
 * 가짜 에이전트가 진짜 Kafka(Redpanda)를 거쳐 ai.tasks/ai.events를 주고받는다.
 */
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT, properties = TestInfra.PROPS)
@Import(TestInfra.class)
class TaskFlowTest {

	private static final Duration WAIT = Duration.ofSeconds(30);

	@LocalServerPort
	int port;

	@Autowired
	JsonMapper json;

	@Autowired
	KafkaTemplate<String, String> kafka;

	private final HttpClient http = HttpClient.newHttpClient();

	@Test
	void planDraftStreamsUntilApprovalThenFinishesAfterManagerApproves() throws Exception {
		assertThat(call("POST", "/api/auth/login", null, "{\"loginId\":\"manager\",\"password\":\"wrong\"}").statusCode())
			.isEqualTo(401);
		assertThat(call("GET", "/api/ai/tasks", null, null).statusCode()).isEqualTo(401);

		String manager = login("manager");
		String executive = login("executive");

		var created = call("POST", "/api/ai/tasks", manager,
				"{\"type\":\"PLAN_DRAFT\",\"shiftId\":\"gwangjin-seongdong:2026-11-03:AM\"}");
		assertThat(created.statusCode()).isEqualTo(202);
		String taskId = json.readTree(created.body()).path("taskId").asString();

		try (var stream = SseStream.open(http, uri("/api/ai/tasks/" + taskId + "/stream"), manager, null)) {
			List<SseEvent> untilApproval = stream.readUntil(e -> e.type().equals("APPROVAL_REQUIRED"));
			assertThat(untilApproval).extracting(SseEvent::type).contains("TOOL_START", "TOKEN");
			assertThat(untilApproval).extracting(SseEvent::id).isSorted().first().isEqualTo(1);
			String approvalId = json.readTree(untilApproval.getLast().data()).path("approval").path("approvalId").asString();

			JsonNode waiting = json.readTree(call("GET", "/api/ai/tasks/" + taskId, manager, null).body());
			assertThat(waiting.path("status").asString()).isEqualTo("WAITING_APPROVAL");
			assertThat(waiting.path("pendingApproval").path("approvalId").asString()).isEqualTo(approvalId);

			String approve = "{\"approvalId\":\"" + approvalId + "\",\"decision\":\"APPROVE\"}";
			var notApprover = call("POST", "/api/ai/tasks/" + taskId + "/approval", executive, approve);
			assertThat(notApprover.statusCode()).isEqualTo(403);
			assertThat(json.readTree(notApprover.body()).path("code").asString()).isEqualTo("NOT_APPROVER");

			assertThat(call("POST", "/api/ai/tasks/" + taskId + "/approval", manager, approve).statusCode()).isEqualTo(202);
			var again = call("POST", "/api/ai/tasks/" + taskId + "/approval", manager, approve);
			assertThat(again.statusCode()).isEqualTo(409);

			List<SseEvent> rest = stream.readUntil(e -> e.type().equals("DONE"));
			assertThat(rest.getLast().id()).isGreaterThan(untilApproval.getLast().id());
		}

		JsonNode done = json.readTree(call("GET", "/api/ai/tasks/" + taskId, manager, null).body());
		assertThat(done.path("status").asString()).isEqualTo("DONE");
		assertThat(done.path("answer").asString()).isNotBlank();

		// 재연결: Last-Event-ID 이후만 다시 받고, 이미 끝난 작업이라 DONE 뒤에 스트림이 닫힌다.
		try (var replay = SseStream.open(http, uri("/api/ai/tasks/" + taskId + "/stream"), manager, "3")) {
			List<SseEvent> events = replay.readUntil(e -> e.type().equals("DONE"));
			assertThat(events.getFirst().id()).isEqualTo(4);
			assertThat(replay.ended()).isTrue();
		}
	}

	@Test
	void routeChangeCanOnlyBeApprovedByThatTrucksDriver() throws Exception {
		String manager = login("manager");
		var created = call("POST", "/api/ai/tasks", manager,
				"{\"type\":\"FIELD_REPORT\",\"shiftId\":\"gwangjin-seongdong:2026-11-03:PM\"}");
		String taskId = json.readTree(created.body()).path("taskId").asString();

		String approvalId;
		try (var stream = SseStream.open(http, uri("/api/ai/tasks/" + taskId + "/stream"), manager, null)) {
			SseEvent approval = stream.readUntil(e -> e.type().equals("APPROVAL_REQUIRED")).getLast();
			JsonNode card = json.readTree(approval.data()).path("approval");
			assertThat(card.path("approver").asString()).isEqualTo("DRIVER");
			assertThat(card.path("truckId").asString()).isEqualTo("truck-1");
			approvalId = card.path("approvalId").asString();
		}

		String body = "{\"approvalId\":\"" + approvalId + "\",\"decision\":\"REJECT\"}";
		// truck-2 기사는 이 작업을 볼 수도 없다. 관리자도 기사 대신 결정할 수 없다.
		assertThat(call("POST", "/api/ai/tasks/" + taskId + "/approval", login("driver2"), body).statusCode()).isEqualTo(404);
		assertThat(call("POST", "/api/ai/tasks/" + taskId + "/approval", manager, body).statusCode()).isEqualTo(403);

		String driver1 = login("driver1");
		assertThat(call("GET", "/api/ai/tasks/" + taskId, driver1, null).statusCode()).isEqualTo(200);
		assertThat(call("POST", "/api/ai/tasks/" + taskId + "/approval", driver1, body).statusCode()).isEqualTo(202);
	}

	@Test
	void driverCannotCreatePlanTasks() throws Exception {
		var res = call("POST", "/api/ai/tasks", login("driver1"),
				"{\"type\":\"PLAN_DRAFT\",\"shiftId\":\"gwangjin-seongdong:2026-11-03:AM\"}");
		assertThat(res.statusCode()).isEqualTo(403);
		assertThat(json.readTree(res.body()).path("code").asString()).isEqualTo("TASK_TYPE_NOT_ALLOWED");
	}

	/** 승인 대기 중에 에이전트가 실패하면 그 승인 요청은 낡은 것이다. 끝난 작업을 다시 재개하지 않는다. */
	@Test
	void approvalAfterTaskFailedIsRejected() throws Exception {
		String manager = login("manager");
		String shiftId = "gwangjin-seongdong:2026-11-04:AM";
		var created = call("POST", "/api/ai/tasks", manager, "{\"type\":\"PLAN_DRAFT\",\"shiftId\":\"" + shiftId + "\"}");
		String taskId = json.readTree(created.body()).path("taskId").asString();

		try (var stream = SseStream.open(http, uri("/api/ai/tasks/" + taskId + "/stream"), manager, null)) {
			SseEvent approval = stream.readUntil(e -> e.type().equals("APPROVAL_REQUIRED")).getLast();
			String approvalId = json.readTree(approval.data()).path("approval").path("approvalId").asString();

			kafka.send(Topics.AI_EVENTS, shiftId, json.writeValueAsString(json.createObjectNode()
				.put("eventId", UUID.randomUUID().toString())
				.put("schemaVersion", 1)
				.put("shiftId", shiftId)
				.put("taskId", taskId)
				.put("type", "FAILED")
				.put("error", "테스트: 에이전트 중단"))).get(10, TimeUnit.SECONDS);
			stream.readUntil(e -> e.type().equals("FAILED"));

			var late = call("POST", "/api/ai/tasks/" + taskId + "/approval", manager,
					"{\"approvalId\":\"" + approvalId + "\",\"decision\":\"APPROVE\"}");
			assertThat(late.statusCode()).isEqualTo(409);
			assertThat(json.readTree(late.body()).path("code").asString()).isEqualTo("APPROVAL_SUPERSEDED");
		}
		JsonNode failed = json.readTree(call("GET", "/api/ai/tasks/" + taskId, manager, null).body());
		assertThat(failed.path("status").asString()).isEqualTo("FAILED");
	}

	@Test
	void listRejectsPageSizeOutOfRange() throws Exception {
		var res = call("GET", "/api/ai/tasks?size=500", login("manager"), null);
		assertThat(res.statusCode()).isEqualTo(400);
		assertThat(json.readTree(res.body()).path("code").asString()).as(res.body()).isEqualTo("INVALID_REQUEST");
	}

	private String login(String loginId) throws Exception {
		var res = call("POST", "/api/auth/login", null,
				"{\"loginId\":\"" + loginId + "\",\"password\":\"test-password\"}");
		assertThat(res.statusCode()).isEqualTo(200);
		return json.readTree(res.body()).path("accessToken").asString();
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

	record SseEvent(int id, String type, String data) {
	}

	/** text/event-stream을 줄 단위로 읽는다. 주석(: ping)은 건너뛴다. */
	static final class SseStream implements AutoCloseable {

		private final Stream<String> lines;
		private final Iterator<String> it;

		private SseStream(Stream<String> lines) {
			this.lines = lines;
			this.it = lines.iterator();
		}

		static SseStream open(HttpClient http, URI uri, String token, String lastEventId) throws Exception {
			var req = HttpRequest.newBuilder(uri).header("Authorization", "Bearer " + token)
				.header("Accept", "text/event-stream");
			if (lastEventId != null) {
				req.header("Last-Event-ID", lastEventId);
			}
			HttpResponse<Stream<String>> res = http.send(req.build(), HttpResponse.BodyHandlers.ofLines());
			assertThat(res.statusCode()).isEqualTo(200);
			return new SseStream(res.body());
		}

		List<SseEvent> readUntil(Predicate<SseEvent> last) {
			List<SseEvent> events = new ArrayList<>();
			long deadline = System.nanoTime() + WAIT.toNanos();
			int id = -1;
			String type = null;
			StringBuilder data = new StringBuilder();
			while (System.nanoTime() < deadline && it.hasNext()) {
				String line;
				try {
					line = it.next();
				}
				catch (UncheckedIOException e) {
					break;
				}
				if (line.isEmpty()) {
					if (type != null) {
						SseEvent event = new SseEvent(id, type, data.toString());
						events.add(event);
						if (last.test(event)) {
							return events;
						}
					}
					id = -1;
					type = null;
					data.setLength(0);
				}
				else if (line.startsWith("id:")) {
					id = Integer.parseInt(line.substring(3).trim());
				}
				else if (line.startsWith("event:")) {
					type = line.substring(6).trim();
				}
				else if (line.startsWith("data:")) {
					data.append(line.substring(5).trim());
				}
			}
			throw new AssertionError("기다린 이벤트가 오지 않았다. 받은 이벤트: " + events);
		}

		boolean ended() {
			return !it.hasNext();
		}

		@Override
		public void close() {
			lines.close();
		}
	}
}
