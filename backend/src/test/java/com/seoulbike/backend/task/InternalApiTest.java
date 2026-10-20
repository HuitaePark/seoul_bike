package com.seoulbike.backend.task;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import java.time.OffsetDateTime;
import java.util.UUID;

import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.web.server.LocalServerPort;
import org.springframework.context.annotation.Import;
import org.springframework.dao.DataAccessException;
import org.springframework.jdbc.core.simple.JdbcClient;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ObjectNode;

import com.seoulbike.backend.TestInfra;
import com.seoulbike.backend.task.TaskFlowTest.SseStream;

/** /internal/** (business-mcp용): 서비스 토큰으로만 열리고, 변경 API는 관리자 승인이 있어야 실행된다 (FR-18). */
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT, properties = TestInfra.PROPS)
@Import(TestInfra.class)
class InternalApiTest {

	private static final String TOKEN = "test-internal-token";
	private static final Duration WAIT = Duration.ofSeconds(30);

	@LocalServerPort
	int port;

	@Autowired
	JsonMapper json;

	@Autowired
	JdbcClient jdbc;

	private final HttpClient http = HttpClient.newHttpClient();

	@Test
	void onlyTheServiceTokenOpensInternal() throws Exception {
		String path = "/internal/shifts/S05:2026-11-07:AM/reports";
		assertThat(code(call("GET", path, null, null, null))).isEqualTo("UNAUTHORIZED");
		assertThat(call("GET", path, null, "wrong-token", null).statusCode()).isEqualTo(401);
		// 사용자 JWT로는 열리지 않는다.
		assertThat(call("GET", path, login("manager"), null, null).statusCode()).isEqualTo(401);
		assertThat(call("GET", path, null, TOKEN, null).statusCode()).isEqualTo(200);
	}

	/** 보고·승인·제약이 한 줄씩 남고, 사람은 역할로만 보이며, 기록은 고치거나 지울 수 없다 (FR-18). */
	@Test
	void auditShowsWhoDidWhatAndCannotBeChanged() throws Exception {
		String shift = "S05:2026-11-08:AM";
		report(login("driver2"), shift, null);
		String manager = login("manager");
		var created = call("POST", "/api/ai/tasks", manager, null,
				"{\"type\":\"REQUEST\",\"shiftId\":\"" + shift + "\",\"text\":\"행사로 ST-201 반납 중지\"}");
		UUID taskId = UUID.fromString(json.readTree(created.body()).path("taskId").asString());
		try (var stream = SseStream.open(http, uri("/api/ai/tasks/" + taskId + "/stream"), manager, null)) {
			UUID approvalId = UUID.fromString(nextCard(stream).path("approvalId").asString());
			assertThat(call("POST", "/api/ai/tasks/" + taskId + "/approval", manager, null,
					"{\"approvalId\":\"" + approvalId + "\",\"decision\":\"APPROVE\"}").statusCode()).isEqualTo(202);
			var body = (ObjectNode) json.readTree(constraint(approvalId, taskId, "STATION_SUSPENDED", null));
			body.put("shiftId", shift);
			assertThat(call("POST", "/internal/constraints", null, TOKEN, json.writeValueAsString(body)).statusCode()).isEqualTo(201);
			assertThat(call("POST", "/internal/constraints", null, TOKEN, json.writeValueAsString(body)).statusCode()).isEqualTo(201);
		}

		var res = call("GET", "/internal/shifts/" + shift + "/audit", null, TOKEN, null);
		JsonNode events = json.readTree(res.body());
		assertThat(events.valueStream().map(e -> e.path("type").asString()))
			.containsExactly("FIELD_REPORT", "APPROVAL_APPROVED", "CONSTRAINT_ADDED");
		assertThat(events.valueStream().map(e -> e.path("actor").path("display").asString()))
			.containsExactly("truck-2 기사", "관리자", "관리자");
		assertThat(events.get(1).path("targetId").asString()).isNotBlank();
		assertThat(res.body()).doesNotContain("u-manager", "u-driver-2", "관제 관리자");

		assertThatThrownBy(() -> jdbc.sql("update audit_events set detail = 'x' where shift_id = :s").param("s", shift).update())
			.isInstanceOf(DataAccessException.class);
		assertThatThrownBy(() -> jdbc.sql("delete from audit_events where shift_id = :s").param("s", shift).update())
			.isInstanceOf(DataAccessException.class);
	}

	@Test
	void shiftReportsHideDriverNamesAndFollowSince() throws Exception {
		String shift = "S05:2026-11-07:AM";
		report(login("driver1"), shift, null);
		report(login("driver2"), shift, "CONSTRUCTION");

		var res = call("GET", "/internal/shifts/" + shift + "/reports", null, TOKEN, null);
		JsonNode reports = json.readTree(res.body());
		assertThat(reports).hasSize(2);
		assertThat(reports.get(0).path("truckId").asString()).isEqualTo("truck-1");
		assertThat(reports.get(0).path("driver").asString()).isEqualTo("truck-1 기사");
		assertThat(reports.get(1).path("issue").asString()).isEqualTo("CONSTRUCTION");
		assertThat(res.body()).doesNotContain("u-driver-1", "기사 1");

		String later = OffsetDateTime.now().plusHours(1).toString().replace("+", "%2B");
		assertThat(json.readTree(call("GET", "/internal/shifts/" + shift + "/reports?since=" + later, null, TOKEN, null)
			.body())).isEmpty();
	}

	@Test
	void constraintNeedsTheApprovedConstraintCardAndRetriesReturnTheSameOne() throws Exception {
		String manager = login("manager");
		var created = call("POST", "/api/ai/tasks", manager,
				null, "{\"type\":\"REQUEST\",\"shiftId\":\"S05:2026-11-07:PM\",\"text\":\"불꽃축제로 ST-201 반납 중지\"}");
		UUID taskId = UUID.fromString(json.readTree(created.body()).path("taskId").asString());

		try (var stream = SseStream.open(http, uri("/api/ai/tasks/" + taskId + "/stream"), manager, null)) {
			JsonNode card = nextCard(stream);
			assertThat(card.path("kind").asString()).isEqualTo("ADD_CONSTRAINT");
			UUID approvalId = UUID.fromString(card.path("approvalId").asString());

			var pending = call("POST", "/internal/constraints", null, TOKEN, constraint(approvalId, taskId, "STATION_SUSPENDED", null));
			assertThat(pending.statusCode()).isEqualTo(403);
			assertThat(code(pending)).isEqualTo("APPROVAL_REQUIRED");
			assertThat(json.readTree(pending.body()).path("detail").asString()).contains("PENDING");

			assertThat(call("POST", "/api/ai/tasks/" + taskId + "/approval", manager, null,
					"{\"approvalId\":\"" + approvalId + "\",\"decision\":\"APPROVE\"}").statusCode()).isEqualTo(202);
			var first = call("POST", "/internal/constraints", null, TOKEN, constraint(approvalId, taskId, "STATION_SUSPENDED", null));
			assertThat(first.statusCode()).isEqualTo(201);
			JsonNode added = json.readTree(first.body());
			assertThat(added.path("stationIds").get(0).asString()).isEqualTo("ST-201");
			var retry = call("POST", "/internal/constraints", null, TOKEN, constraint(approvalId, taskId, "STATION_SUSPENDED", null));
			assertThat(json.readTree(retry.body()).path("constraintId").asString())
				.isEqualTo(added.path("constraintId").asString());
			// 같은 승인을 다른 내용에 쓰거나, 다른 근무조를 바꾸는 데 쓸 수 없다.
			var otherContent = (ObjectNode) json.readTree(constraint(approvalId, taskId, "STATION_BLOCKED", null));
			var reused = call("POST", "/internal/constraints", null, TOKEN, json.writeValueAsString(otherContent));
			assertThat(reused.statusCode()).isEqualTo(409);
			assertThat(code(reused)).isEqualTo("APPROVAL_ALREADY_USED");
			var otherShift = (ObjectNode) json.readTree(constraint(approvalId, taskId, "STATION_SUSPENDED", null));
			otherShift.put("shiftId", "S05:2026-11-07:AM");
			var wrongShift = call("POST", "/internal/constraints", null, TOKEN, json.writeValueAsString(otherShift));
			assertThat(wrongShift.statusCode()).isEqualTo(403);
			assertThat(json.readTree(wrongShift.body()).path("detail").asString()).contains("근무조 S05:2026-11-07:PM");

			// 다른 종류의 카드, 다른 작업의 승인으로는 제약을 넣을 수 없다.
			UUID routeCard = UUID.fromString(nextCard(stream).path("approvalId").asString());
			assertThat(code(call("POST", "/internal/constraints", null, TOKEN, constraint(routeCard, taskId, "STATION_SUSPENDED", null))))
				.isEqualTo("APPROVAL_REQUIRED");
			assertThat(call("POST", "/internal/constraints", null, TOKEN,
					constraint(approvalId, UUID.randomUUID(), "STATION_SUSPENDED", null)).statusCode()).isEqualTo(403);
		}
	}

	@Test
	void constraintInputIsValidatedBeforeApproval() throws Exception {
		UUID any = UUID.randomUUID();
		var backwards = (ObjectNode) json.readTree(constraint(any, any, "STATION_SUSPENDED", null));
		backwards.put("validTo", "2026-11-07T08:00:00+09:00");
		assertThat(code(call("POST", "/internal/constraints", null, TOKEN, json.writeValueAsString(backwards))))
			.isEqualTo("INVALID_REQUEST");
		assertThat(code(call("POST", "/internal/constraints", null, TOKEN, constraint(any, any, "TARGET_ADJUST", null))))
			.isEqualTo("INVALID_REQUEST");
		var noStations = (ObjectNode) json.readTree(constraint(any, any, "STATION_SUSPENDED", null));
		noStations.putArray("stationIds");
		assertThat(call("POST", "/internal/constraints", null, TOKEN, json.writeValueAsString(noStations)).statusCode())
			.isEqualTo(400);
		assertThat(code(call("POST", "/internal/constraints", null, TOKEN, constraint(any, any, "INVENTORY_OVERRIDE", -1))))
			.isEqualTo("INVALID_REQUEST");
	}

	private String constraint(UUID approvalId, UUID taskId, String type, Integer value) {
		ObjectNode body = json.createObjectNode()
			.put("approvalId", approvalId.toString())
			.put("taskId", taskId.toString())
			.put("shiftId", "S05:2026-11-07:PM")
			.put("type", type)
			.put("value", value)
			.put("validFrom", "2026-11-07T18:00:00+09:00")
			.put("validTo", "2026-11-07T22:00:00+09:00")
			.put("reason", "불꽃축제 반납 중지 (서울시 공지)");
		body.putArray("stationIds").add("ST-201");
		return json.writeValueAsString(body);
	}

	private void report(String token, String shiftId, String issue) throws Exception {
		ObjectNode body = json.createObjectNode().put("shiftId", shiftId).put("stationId", "ST-102").put("actualBikes", 3)
			.put("issue", issue);
		assertThat(call("POST", "/api/reports", token, null, json.writeValueAsString(body)).statusCode()).isEqualTo(202);
	}

	private JsonNode nextCard(SseStream stream) {
		return json.readTree(stream.readUntil(e -> e.type().equals("APPROVAL_REQUIRED")).getLast().data()).path("approval");
	}

	private String login(String loginId) throws Exception {
		var res = call("POST", "/api/auth/login", null, null,
				"{\"loginId\":\"" + loginId + "\",\"password\":\"test-password\"}");
		return json.readTree(res.body()).path("accessToken").asString();
	}

	private String code(HttpResponse<String> res) {
		return json.readTree(res.body()).path("code").asString();
	}

	private URI uri(String path) {
		return URI.create("http://localhost:" + port + path);
	}

	private HttpResponse<String> call(String method, String path, String bearer, String internalToken, String body)
			throws Exception {
		var req = HttpRequest.newBuilder(uri(path)).timeout(WAIT)
			.method(method, body == null ? HttpRequest.BodyPublishers.noBody() : HttpRequest.BodyPublishers.ofString(body));
		if (body != null) {
			req.header("Content-Type", "application/json");
		}
		if (bearer != null) {
			req.header("Authorization", "Bearer " + bearer);
		}
		if (internalToken != null) {
			req.header("X-Internal-Token", internalToken);
		}
		return http.send(req.build(), HttpResponse.BodyHandlers.ofString());
	}
}
