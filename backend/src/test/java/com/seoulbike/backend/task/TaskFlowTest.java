package com.seoulbike.backend.task;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

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
import org.springframework.boot.test.web.server.LocalServerPort;
import org.springframework.kafka.core.KafkaTemplate;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ObjectNode;

import com.seoulbike.backend.DeadLetters;
import com.seoulbike.backend.IntegrationTest;
import com.seoulbike.backend.common.ApiException;
import com.seoulbike.backend.route.RouteChangeGrade;

/**
 * M2 완료 기준: 로그인 → 작업 202 → SSE로 진행 이벤트 → 승인 → 같은 스트림에서 DONE.
 * 가짜 에이전트가 진짜 Kafka(Redpanda)를 거쳐 ai.tasks/ai.events를 주고받는다.
 */
@IntegrationTest
class TaskFlowTest {

	private static final Duration WAIT = Duration.ofSeconds(30);

	@LocalServerPort
	int port;

	@Autowired
	JsonMapper json;

	@Autowired
	KafkaTemplate<String, String> kafka;

	@Autowired
	ChangeApprovals changeApprovals;

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
		var reported = report(login("driver1"), "gwangjin-seongdong:2026-11-03:PM", "BLOCKED");
		String taskId = json.readTree(reported.body()).path("taskId").asString();

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

	/** 보고한 기사의 트럭(JWT)이 명령의 requestedBy.truckId로 가서, 그 트럭 경로 카드가 그 기사에게 온다. */
	@Test
	void fieldReportReroutesTheReportersTruck() throws Exception {
		String driver2 = login("driver2");
		var reported = report(driver2, "gwangjin-seongdong:2026-11-06:AM", "CONSTRUCTION");
		assertThat(reported.statusCode()).isEqualTo(202);
		JsonNode accepted = json.readTree(reported.body());
		assertThat(accepted.path("reportId").asString()).isNotBlank();
		UUID taskId = UUID.fromString(accepted.path("taskId").asString());

		try (var stream = SseStream.open(http, uri("/api/ai/tasks/" + taskId + "/stream"), driver2, null)) {
			JsonNode card = nextCard(stream);
			assertThat(card.path("approver").asString()).isEqualTo("DRIVER");
			assertThat(card.path("truckId").asString()).isEqualTo("truck-2");
			assertThat(decide(taskId, driver2, card)).isEqualTo(202);
			assertThat(stream.readUntil(e -> e.type().equals("DONE") || e.type().equals("FAILED")).getLast().type())
				.isEqualTo("DONE");
		}
	}

	/** 보고는 기사만, issue가 있을 때만 재경로 작업. 현장 보고 작업을 작업 API로 만들 수는 없다 (G13). */
	@Test
	void reportsAreForDriversAndOnlyIssuesStartReroutes() throws Exception {
		String manager = login("manager");
		var progressOnly = report(login("driver1"), "gwangjin-seongdong:2026-11-06:PM", null);
		assertThat(progressOnly.statusCode()).isEqualTo(202);
		assertThat(json.readTree(progressOnly.body()).path("taskId").isNull()).isTrue();

		assertThat(report(manager, "gwangjin-seongdong:2026-11-06:PM", "BLOCKED").statusCode()).isEqualTo(403);
		var viaTasks = call("POST", "/api/ai/tasks", manager,
				"{\"type\":\"FIELD_REPORT\",\"shiftId\":\"gwangjin-seongdong:2026-11-06:PM\"}");
		assertThat(viaTasks.statusCode()).isEqualTo(400);
		assertThat(json.readTree(viaTasks.body()).path("code").asString()).isEqualTo("USE_REPORTS_API");

		var negative = call("POST", "/api/reports", login("driver1"),
				"{\"shiftId\":\"s\",\"stationId\":\"ST-1\",\"actualBikes\":-1}");
		assertThat(negative.statusCode()).isEqualTo(400);
		var unknownIssue = call("POST", "/api/reports", login("driver1"),
				"{\"shiftId\":\"s\",\"stationId\":\"ST-1\",\"actualBikes\":1,\"issue\":\"FLOOD\"}");
		assertThat(unknownIssue.statusCode()).isEqualTo(400);
		assertThat(json.readTree(unknownIssue.body()).path("code").asString()).isEqualTo("INVALID_REQUEST");
	}

	@Test
	void driverCannotCreatePlanTasks() throws Exception {
		var res = call("POST", "/api/ai/tasks", login("driver1"),
				"{\"type\":\"PLAN_DRAFT\",\"shiftId\":\"gwangjin-seongdong:2026-11-03:AM\"}");
		assertThat(res.statusCode()).isEqualTo(403);
		assertThat(json.readTree(res.body()).path("code").asString()).isEqualTo("TASK_TYPE_NOT_ALLOWED");
	}

	private HttpResponse<String> report(String token, String shiftId, String issue) throws Exception {
		ObjectNode body = json.createObjectNode()
			.put("shiftId", shiftId)
			.put("stationId", "ST-102")
			.put("actualBikes", 0)
			.put("issue", issue)
			.put("memo", "입구 공사로 진입 불가");
		return call("POST", "/api/reports", token, json.writeValueAsString(body));
	}

	/**
	 * 승인 대기 중에 에이전트가 실패하면 그 승인 요청은 낡은 것이다. 끝난 작업을 다시 재개하지 않는다.
	 * 화면으로는 계약 필드(code 포함)만 가고, agent 확장(harness)과 봉투는 가지 않는다.
	 */
	@Test
	void approvalAfterTaskFailedIsRejected() throws Exception {
		String manager = login("manager");
		String shiftId = "gwangjin-seongdong:2026-11-04:AM";
		var created = call("POST", "/api/ai/tasks", manager, "{\"type\":\"PLAN_DRAFT\",\"shiftId\":\"" + shiftId + "\"}");
		String taskId = json.readTree(created.body()).path("taskId").asString();

		try (var stream = SseStream.open(http, uri("/api/ai/tasks/" + taskId + "/stream"), manager, null)) {
			SseEvent approval = stream.readUntil(e -> e.type().equals("APPROVAL_REQUIRED")).getLast();
			String approvalId = json.readTree(approval.data()).path("approval").path("approvalId").asString();

			ObjectNode failedEvent = json.createObjectNode()
				.put("eventId", UUID.randomUUID().toString())
				.put("schemaVersion", 1)
				.put("shiftId", shiftId)
				.put("taskId", taskId)
				.put("type", "FAILED")
				.put("error", "테스트: 에이전트 중단")
				.put("code", "LOOP_TIMEOUT");
			failedEvent.putObject("harness").put("runId", "run-1").put("totalTokens", 1234);
			kafka.send(Topics.AI_EVENTS, shiftId, json.writeValueAsString(failedEvent)).get(10, TimeUnit.SECONDS);
			JsonNode failedData = json.readTree(stream.readUntil(e -> e.type().equals("FAILED")).getLast().data());
			assertThat(failedData.path("code").asString()).isEqualTo("LOOP_TIMEOUT");
			assertThat(failedData.has("harness")).isFalse();
			assertThat(failedData.has("eventId")).isFalse();

			var late = call("POST", "/api/ai/tasks/" + taskId + "/approval", manager,
					"{\"approvalId\":\"" + approvalId + "\",\"decision\":\"APPROVE\"}");
			assertThat(late.statusCode()).isEqualTo(409);
			assertThat(json.readTree(late.body()).path("code").asString()).isEqualTo("APPROVAL_SUPERSEDED");
		}
		JsonNode failed = json.readTree(call("GET", "/api/ai/tasks/" + taskId, manager, null).body());
		assertThat(failed.path("status").asString()).isEqualTo("FAILED");
	}

	/**
	 * 팀 합의 5: backend가 내보내는 시각은 +09:00이다. 화면(REST·SSE)과 agent(ai.tasks)가 같은 서울 시각을 본다.
	 * agent가 Z로 보낸 시각도 SSE로는 같은 순간의 +09:00으로 나간다.
	 */
	@Test
	void emittedTimesCarrySeoulOffset() throws Exception {
		String manager = login("manager");
		String shiftId = "N01:2026-11-09:EVE";
		var created = call("POST", "/api/ai/tasks", manager,
				"{\"type\":\"REQUEST\",\"shiftId\":\"" + shiftId + "\",\"text\":\"시각 오프셋 확인\"}");
		assertThat(created.statusCode()).isEqualTo(202);
		String taskId = json.readTree(created.body()).path("taskId").asString();

		JsonNode task = json.readTree(call("GET", "/api/ai/tasks/" + taskId, manager, null).body());
		assertThat(task.path("createdAt").asString()).endsWith("+09:00");
		assertThat(task.path("updatedAt").asString()).endsWith("+09:00");

		JsonNode command = json.readTree(DeadLetters.await(kafka, Topics.AI_TASKS, taskId).value());
		assertThat(command.path("occurredAt").asString()).endsWith("+09:00");

		try (var stream = SseStream.open(http, uri("/api/ai/tasks/" + taskId + "/stream"), manager, null)) {
			String marker = "offset-" + UUID.randomUUID();
			ObjectNode token = json.createObjectNode()
				.put("eventId", UUID.randomUUID().toString())
				.put("schemaVersion", 1)
				.put("shiftId", shiftId)
				.put("taskId", taskId)
				.put("type", "TOKEN")
				.put("occurredAt", "2026-11-09T10:00:00Z")
				.put("token", marker);
			kafka.send(Topics.AI_EVENTS, shiftId, json.writeValueAsString(token)).get(10, TimeUnit.SECONDS);
			JsonNode data = json.readTree(stream.readUntil(e -> e.data().contains(marker)).getLast().data());
			assertThat(data.path("occurredAt").asString()).isEqualTo("2026-11-09T19:00:00+09:00");
		}
	}

	@Test
	void listRejectsPageSizeOutOfRange() throws Exception {
		var res = call("GET", "/api/ai/tasks?size=500", login("manager"), null);
		assertThat(res.statusCode()).isEqualTo(400);
		assertThat(json.readTree(res.body()).path("code").asString()).as(res.body()).isEqualTo("INVALID_REQUEST");
	}

	/** 관리자 요청에서 비롯된 경로 변경은 큰 변경이다: 관리자 카드 → 기사 카드 → 적용 (같은 후보, 같은 작업). */
	@Test
	void majorRouteChangeNeedsManagerThenTrucksDriver() throws Exception {
		String manager = login("manager");
		String driver1 = login("driver1");
		var created = call("POST", "/api/ai/tasks", manager,
				"{\"type\":\"REQUEST\",\"shiftId\":\"gwangjin-seongdong:2026-11-05:AM\",\"text\":\"행사로 ST-201 수요 증가\"}");
		UUID taskId = UUID.fromString(json.readTree(created.body()).path("taskId").asString());

		try (var stream = SseStream.open(http, uri("/api/ai/tasks/" + taskId + "/stream"), manager, null)) {
			JsonNode constraint = nextCard(stream);
			assertThat(constraint.path("kind").asString()).isEqualTo("ADD_CONSTRAINT");
			assertThat(decide(taskId, manager, constraint)).isEqualTo(202);

			JsonNode managerCard = nextCard(stream);
			assertThat(managerCard.path("kind").asString()).isEqualTo("ROUTE_CHANGE");
			assertThat(managerCard.path("approver").asString()).isEqualTo("MANAGER");
			assertThat(managerCard.path("grade").asString()).isEqualTo("MAJOR");
			assertThat(managerCard.path("requiredApprovers").valueStream().map(JsonNode::asString))
				.containsExactly("MANAGER", "DRIVER");
			String candidateId = managerCard.path("candidateId").asString();
			UUID managerCardId = UUID.fromString(managerCard.path("approvalId").asString());

			// 기사는 자기 트럭 변경이 오는 것을 볼 수 있지만, 관리자 카드를 대신 결정할 수는 없다.
			assertThat(decide(taskId, driver1, managerCard)).isEqualTo(403);
			assertThatThrownBy(() -> changeApprovals.requireRouteChange(taskId, managerCardId, "truck-1", candidateId,
					RouteChangeGrade.MAJOR))
				.isInstanceOfSatisfying(ApiException.class, e -> assertThat(e.getMessage()).contains("관리자 승인이 없다"));
			assertThat(decide(taskId, manager, managerCard)).isEqualTo(202);

			JsonNode driverCard = nextCard(stream);
			assertThat(driverCard.path("approver").asString()).isEqualTo("DRIVER");
			assertThat(driverCard.path("truckId").asString()).isEqualTo("truck-1");
			assertThat(driverCard.path("candidateId").asString()).isEqualTo(candidateId);
			assertThat(decide(taskId, driver1, driverCard)).isEqualTo(202);

			// 가짜 에이전트는 적용 전에 같은 확인 규칙을 부른다. 통과해야 DONE이다.
			SseEvent end = stream.readUntil(e -> e.type().equals("DONE") || e.type().equals("FAILED")).getLast();
			assertThat(end.type()).as(end.data()).isEqualTo("DONE");
			assertThat(json.readTree(end.data()).path("answer").asString()).contains("truck-1 경로 변경을 적용");
		}
	}

	/** 적용 확인은 backend가 계산한 등급을 쓴다. 카드에 적힌 등급, 다른 트럭, 마지막이 아닌 카드는 통과하지 못한다. */
	@Test
	void applyCheckFollowsBackendGradeAndTruck() throws Exception {
		String manager = login("manager");
		var reported = report(login("driver1"), "gwangjin-seongdong:2026-11-05:PM", "BLOCKED");
		UUID taskId = UUID.fromString(json.readTree(reported.body()).path("taskId").asString());

		JsonNode driverCard;
		try (var stream = SseStream.open(http, uri("/api/ai/tasks/" + taskId + "/stream"), manager, null)) {
			driverCard = nextCard(stream);
			assertThat(driverCard.path("grade").asString()).isEqualTo("MINOR");
			assertThat(driverCard.path("requiredApprovers").valueStream().map(JsonNode::asString)).containsExactly("DRIVER");
			assertThat(decide(taskId, login("driver1"), driverCard)).isEqualTo(202);
			assertThat(stream.readUntil(e -> e.type().equals("DONE") || e.type().equals("FAILED")).getLast().type())
				.isEqualTo("DONE");
		}
		String candidateId = driverCard.path("candidateId").asString();
		UUID driverCardId = UUID.fromString(driverCard.path("approvalId").asString());

		changeApprovals.requireRouteChange(taskId, driverCardId, "truck-1", candidateId, RouteChangeGrade.MINOR);
		assertThatThrownBy(() -> changeApprovals.requireRouteChange(taskId, driverCardId, "truck-1", candidateId,
				RouteChangeGrade.MAJOR))
			.isInstanceOfSatisfying(ApiException.class, e -> assertThat(e.code()).isEqualTo("APPROVAL_REQUIRED"));
		assertThatThrownBy(() -> changeApprovals.requireRouteChange(taskId, driverCardId, "truck-2", candidateId,
				RouteChangeGrade.MINOR))
			.isInstanceOfSatisfying(ApiException.class, e -> assertThat(e.getMessage()).contains("truck-2 기사 승인이 없다"));
		assertThatThrownBy(() -> changeApprovals.requireRouteChange(taskId, UUID.randomUUID(), "truck-1", candidateId,
				RouteChangeGrade.MINOR))
			.isInstanceOfSatisfying(ApiException.class, e -> assertThat(e.getMessage()).contains("마지막 승인이 아니다"));
		assertThatThrownBy(() -> changeApprovals.requireRouteChange(taskId, driverCardId, "truck-1", candidateId,
				RouteChangeGrade.NONE))
			.isInstanceOfSatisfying(ApiException.class, e -> assertThat(e.code()).isEqualTo("NOTHING_TO_APPLY"));
	}

	private JsonNode nextCard(SseStream stream) {
		SseEvent event = stream.readUntil(e -> e.type().equals("APPROVAL_REQUIRED")).getLast();
		return json.readTree(event.data()).path("approval");
	}

	private int decide(UUID taskId, String token, JsonNode card) throws Exception {
		return call("POST", "/api/ai/tasks/" + taskId + "/approval", token,
				"{\"approvalId\":\"" + card.path("approvalId").asString() + "\",\"decision\":\"APPROVE\"}").statusCode();
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
