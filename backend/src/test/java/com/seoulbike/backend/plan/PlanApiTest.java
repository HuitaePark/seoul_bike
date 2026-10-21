package com.seoulbike.backend.plan;

import static org.assertj.core.api.Assertions.assertThat;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import java.time.LocalDate;
import java.time.ZoneId;
import java.util.UUID;

import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.web.server.LocalServerPort;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.security.crypto.password.PasswordEncoder;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ArrayNode;
import tools.jackson.databind.node.ObjectNode;

import com.seoulbike.backend.IntegrationTest;

/**
 * 계획 조회·활성화·경로 적용 (internal-api, openapi). 계획·후보·승인 행은 DB에 바로 넣는다: 승인 흐름 자체는 TaskFlowTest가 보고,
 * 여기서는 변경 API가 그 승인을 어떻게 확인하고 버전을 어떻게 바꾸는지만 본다. 테스트마다 새 근무조를 쓴다.
 */
@IntegrationTest
class PlanApiTest {

	private static final String TOKEN = "test-internal-token";

	@LocalServerPort
	int port;

	@Autowired
	JsonMapper json;

	@Autowired
	JdbcClient jdbc;

	@Autowired
	PasswordEncoder passwords;

	private final HttpClient http = HttpClient.newHttpClient();

	@Test
	void activationNeedsThisPlansManagerApprovalAndClosesThePreviousActive() throws Exception {
		String shift = newShift();
		String v1 = plan(shift, 1, "ACTIVE", routes(route("TR-01", stop("ST-1", "PICKUP", 5))), null);
		String v2 = plan(shift, 2, "PROPOSED", routes(route("TR-01", stop("ST-2", "PICKUP", 5))), 1);
		UUID task = task(shift, "PLAN_DRAFT");

		UUID constraintCard = approval(task, "ADD_CONSTRAINT", "MANAGER", null, null, null, "u-manager");
		assertThat(code(activate(v2, constraintCard, task))).isEqualTo("APPROVAL_REQUIRED");
		UUID otherPlanCard = approval(task, "SUBMIT_PLAN", "MANAGER", null, null, "PL-other", "u-manager");
		var otherPlan = activate(v2, otherPlanCard, task);
		assertThat(otherPlan.statusCode()).isEqualTo(403);
		assertThat(detail(otherPlan)).contains("계획 PL-other의 승인이다");
		// 어느 계획인지 적히지 않은 승인으로는 아무 계획도 활성화하지 않는다.
		UUID noPlanCard = approval(task, "SUBMIT_PLAN", "MANAGER", null, null, null, "u-manager");
		assertThat(code(activate(v2, noPlanCard, task))).isEqualTo("APPROVAL_REQUIRED");
		assertThat(activate("PL-" + UUID.randomUUID(), noPlanCard, task).statusCode()).isEqualTo(404);

		UUID card = approval(task, "SUBMIT_PLAN", "MANAGER", null, null, v2, "u-manager");
		var first = activate(v2, card, task);
		assertThat(first.statusCode()).isEqualTo(200);
		JsonNode activated = json.readTree(first.body());
		assertThat(activated.path("planId").asString()).isEqualTo(v2);
		assertThat(activated.path("status").asString()).isEqualTo("ACTIVE");
		assertThat(activated.path("version").asInt()).isEqualTo(2);
		assertThat(status(v1)).isEqualTo("CLOSED");
		assertThat(jdbc.sql("select activated_by from plans where plan_id = :p").param("p", v2).query(String.class).single())
			.isEqualTo("u-manager");

		// 재시도는 같은 계획을 돌려주고 기록을 더하지 않는다. 같은 승인을 다른 계획에 쓸 수는 없다.
		var retry = activate(v2, card, task);
		assertThat(retry.statusCode()).isEqualTo(200);
		assertThat(json.readTree(retry.body()).path("planId").asString()).isEqualTo(v2);
		JsonNode audit = json.readTree(call("GET", "/internal/shifts/" + shift + "/audit", null, TOKEN, null).body());
		assertThat(audit).hasSize(1);
		assertThat(audit.get(0).path("type").asString()).isEqualTo("PLAN_ACTIVATED");
		assertThat(audit.get(0).path("actor").path("display").asString()).isEqualTo("관리자");
		assertThat(audit.get(0).path("targetId").asString()).isEqualTo(v2);
		assertThat(audit.get(0).path("detail").asString()).startsWith("v2");
		String v3 = plan(shift, 3, "PROPOSED", routes(route("TR-01", stop("ST-3", "PICKUP", 5))), 2);
		assertThat(code(activate(v3, card, task))).isEqualTo("APPROVAL_ALREADY_USED");

		// 닫힌 계획, 그리고 지금 활성 계획보다 옛 버전을 기준으로 계산한 후보는 활성화하지 않는다.
		var closed = activate(v1, approval(task, "SUBMIT_PLAN", "MANAGER", null, null, v1, "u-manager"), task);
		assertThat(closed.statusCode()).isEqualTo(409);
		assertThat(code(closed)).isEqualTo("PLAN_SUPERSEDED");
		String stale = plan(shift, 4, "PROPOSED", routes(route("TR-01", stop("ST-4", "PICKUP", 5))), 1);
		var staleRes = activate(stale, approval(task, "SUBMIT_PLAN", "MANAGER", null, null, stale, "u-manager"), task);
		assertThat(code(staleRes)).isEqualTo("PLAN_SUPERSEDED");
		assertThat(detail(staleRes)).contains("request_route_plan");
		assertThat(json.readTree(call("GET", "/internal/shifts/" + shift + "/plan", null, TOKEN, null).body())
			.path("planId").asString()).isEqualTo(v2);
	}

	@Test
	void routeApplyMakesANewActiveVersionWithOnlyThatTruckChanged() throws Exception {
		String shift = newShift();
		JsonNode truck2 = route("TR-02", stop("ST-3", "PICKUP", 4));
		String v1 = plan(shift, 1, "ACTIVE",
				routes(route("TR-01", stop("ST-1", "PICKUP", 5), stop("ST-2", "DROPOFF", 5)), truck2), null);
		JsonNode changed = route("TR-01", stop("ST-1", "PICKUP", 7), stop("ST-2", "DROPOFF", 7));
		// 현장 보고 작업에서 한 트럭만 조금 바뀐 후보라 MINOR: 그 트럭 기사의 수락만 있으면 된다.
		UUID task = task(shift, "FIELD_REPORT");
		String candidate = plan(shift, 2, "PROPOSED", routes(changed, truck2), 1, task);

		var noDriver = apply("TR-01", UUID.randomUUID(), task, candidate);
		assertThat(noDriver.statusCode()).isEqualTo(403);
		assertThat(detail(noDriver)).contains("TR-01 기사 승인이 없다");
		UUID otherTruck = approval(task, "ROUTE_CHANGE", "DRIVER", "TR-02", candidate, null, "u-driver-2");
		assertThat(code(apply("TR-01", otherTruck, task, candidate))).isEqualTo("APPROVAL_REQUIRED");

		UUID accepted = approval(task, "ROUTE_CHANGE", "DRIVER", "TR-01", candidate, null, "u-driver-1");
		var first = apply("TR-01", accepted, task, candidate);
		assertThat(first.statusCode()).isEqualTo(200);
		assertThat(json.readTree(first.body())).isEqualTo(changed);

		JsonNode active = json.readTree(call("GET", "/internal/shifts/" + shift + "/plan", null, TOKEN, null).body());
		assertThat(active.path("version").asInt()).isEqualTo(3);
		assertThat(active.path("routes")).containsExactly(changed, truck2);
		assertThat(status(v1)).isEqualTo("CLOSED");
		assertThat(status(candidate)).isEqualTo("PROPOSED");
		assertThat(jdbc.sql("select activated_by from plans where shift_id = :s and status = 'ACTIVE'")
			.param("s", shift)
			.query(String.class)
			.single()).isEqualTo("u-driver-1");

		// 재시도는 같은 경로, 새 버전·기록 없음. 같은 승인으로 다른 트럭을 바꿀 수 없다.
		var retry = apply("TR-01", accepted, task, candidate);
		assertThat(retry.statusCode()).isEqualTo(200);
		assertThat(json.readTree(retry.body())).isEqualTo(changed);
		assertThat(jdbc.sql("select count(*) from plans where shift_id = :s").param("s", shift).query(Integer.class).single())
			.isEqualTo(3);
		JsonNode audit = json.readTree(call("GET", "/internal/shifts/" + shift + "/audit", null, TOKEN, null).body());
		assertThat(audit).hasSize(1);
		assertThat(audit.get(0).path("type").asString()).isEqualTo("ROUTE_CHANGED");
		assertThat(audit.get(0).path("actor").path("display").asString()).isEqualTo("TR-01 기사");
		assertThat(audit.get(0).path("targetId").asString()).isEqualTo("TR-01");
		assertThat(audit.get(0).path("detail").asString()).contains(candidate, "v3");
		assertThat(code(apply("TR-02", accepted, task, candidate))).isEqualTo("APPROVAL_ALREADY_USED");

		// 후보는 v1 기준인데 활성 계획이 v3이 됐다. 같은 후보를 새 승인으로 다시 적용하지 않는다.
		UUID again = approval(task, "ROUTE_CHANGE", "DRIVER", "TR-01", candidate, null, "u-driver-1");
		var stale = apply("TR-01", again, task, candidate);
		assertThat(stale.statusCode()).isEqualTo(409);
		assertThat(code(stale)).isEqualTo("STALE_CANDIDATE");
		assertThat(detail(stale)).contains("request_route_plan");
	}

	/** 적용은 후보를 만든 작업의 승인으로만 한다. 등급의 출발점도 그 작업이다 (호출자가 보낸 taskId가 아니다). */
	@Test
	void routeApplyUsesTheTaskThatMadeTheCandidate() throws Exception {
		String shift = newShift();
		JsonNode truck2 = route("TR-02", stop("ST-3", "PICKUP", 4));
		plan(shift, 1, "ACTIVE", routes(route("TR-01", stop("ST-1", "PICKUP", 5)), truck2), null);
		JsonNode changed = route("TR-01", stop("ST-1", "PICKUP", 6));
		UUID request = task(shift, "REQUEST");
		String candidate = plan(shift, 2, "PROPOSED", routes(changed, truck2), 1, request);

		// 현장 보고 작업의 기사 카드로는 관리자 요청 후보(MAJOR)를 MINOR처럼 적용할 수 없다.
		UUID fieldReport = task(shift, "FIELD_REPORT");
		UUID otherTaskCard = approval(fieldReport, "ROUTE_CHANGE", "DRIVER", "TR-01", candidate, null, "u-driver-1");
		var otherTask = apply("TR-01", otherTaskCard, fieldReport, candidate);
		assertThat(otherTask.statusCode()).isEqualTo(403);
		assertThat(code(otherTask)).isEqualTo("APPROVAL_REQUIRED");
		assertThat(detail(otherTask)).contains("작업 " + request + "에서 만든 후보다");

		// 만든 작업에서도 관리자 요청이라 MAJOR: 기사 수락만으로는 안 되고 관리자 → 기사 순서가 필요하다.
		UUID driverOnly = approval(request, "ROUTE_CHANGE", "DRIVER", "TR-01", candidate, null, "u-driver-1");
		assertThat(detail(apply("TR-01", driverOnly, request, candidate))).contains("관리자 승인이 없다");
		approval(request, "ROUTE_CHANGE", "MANAGER", null, candidate, null, "u-manager");
		UUID accepted = approval(request, "ROUTE_CHANGE", "DRIVER", "TR-01", candidate, null, "u-driver-1");
		var applied = apply("TR-01", accepted, request, candidate);
		assertThat(applied.statusCode()).isEqualTo(200);
		assertThat(json.readTree(applied.body())).isEqualTo(changed);
	}

	@Test
	void agentReadsTheActivePlanAndScreensReadTheLatestVersion() throws Exception {
		String shift = newShift();
		var none = call("GET", "/internal/shifts/" + shift + "/plan", null, TOKEN, null);
		assertThat(none.statusCode()).isEqualTo(404);
		assertThat(code(none)).isEqualTo("SHIFT_NOT_FOUND");
		assertThat(detail(none)).contains("활성 계획이 없다");
		String manager = login("manager");
		assertThat(code(call("GET", "/api/shifts/" + shift + "/plan", manager, null, null))).isEqualTo("NOT_FOUND");

		String active = plan(shift, 1, "ACTIVE", routes(route("TR-01", stop("ST-1", "PICKUP", 5))), null);
		String proposed = plan(shift, 2, "PROPOSED", routes(route("TR-01", stop("ST-2", "PICKUP", 5))), 1);
		JsonNode current = json.readTree(call("GET", "/internal/shifts/" + shift + "/plan", null, TOKEN, null).body());
		assertThat(current.path("planId").asString()).isEqualTo(active);
		assertThat(current.path("routes").get(0).path("stops").get(0).path("stationId").asString()).isEqualTo("ST-1");
		assertThat(current.path("unserved").isArray()).isTrue();
		for (String token : new String[] { manager, login("executive") }) {
			JsonNode latest = json.readTree(call("GET", "/api/shifts/" + shift + "/plan", token, null, null).body());
			assertThat(latest.path("planId").asString()).isEqualTo(proposed);
			assertThat(latest.path("status").asString()).isEqualTo("PROPOSED");
		}
		// 근무조 계획에는 모든 트럭 경로가 있다. 기사는 자기 경로만 본다 (NFR-06).
		assertThat(call("GET", "/api/shifts/" + shift + "/plan", login("driver1"), null, null).statusCode()).isEqualTo(403);
	}

	@Test
	void driverSeesOnlyTheirOwnTrucksActiveRoute() throws Exception {
		String truck = "TR-" + (100_000 + (int) (Math.random() * 900_000));
		String driver = newDriver(truck);
		var none = call("GET", "/api/drivers/me/route", driver, null, null);
		assertThat(none.statusCode()).isEqualTo(404);
		assertThat(detail(none)).isEqualTo("오늘 경로가 없다");

		// 지난 근무조의 ACTIVE 계획은 닫히지 않고 남지만 오늘 경로가 아니다.
		String yesterday = LocalDate.now(ZoneId.of("Asia/Seoul")).minusDays(1).toString();
		plan(newShift(yesterday), 1, "ACTIVE", routes(route(truck, stop("ST-6", "PICKUP", 2))), null);
		assertThat(call("GET", "/api/drivers/me/route", driver, null, null).statusCode()).isEqualTo(404);

		JsonNode own = route(truck, stop("ST-7", "DROPOFF", 3));
		String today = LocalDate.now(ZoneId.of("Asia/Seoul")).toString();
		plan(newShift(today), 1, "ACTIVE", routes(route("TR-01", stop("ST-1", "PICKUP", 5)), own), null);
		var res = call("GET", "/api/drivers/me/route", driver, null, null);
		assertThat(res.statusCode()).isEqualTo(200);
		assertThat(json.readTree(res.body())).isEqualTo(own);

		assertThat(call("GET", "/api/drivers/me/route", login("manager"), null, null).statusCode()).isEqualTo(403);
	}

	// ── 행 넣기 ────────────────────────────

	private static String newShift() {
		return newShift("2026-12-01");
	}

	private static String newShift(String day) {
		return "S9" + UUID.randomUUID().toString().substring(0, 6) + ":" + day + ":AM";
	}

	private String plan(String shiftId, int version, String status, ArrayNode routes, Integer basedOn) {
		return plan(shiftId, version, status, routes, basedOn, null);
	}

	/** 계획 한 행. basedOn이 있으면 그 활성 version을 기준으로 taskId가 요청한 계산 요청(solve_requests)도 넣는다. */
	private String plan(String shiftId, int version, String status, ArrayNode routes, Integer basedOn, UUID taskId) {
		String planId = "PL-" + UUID.randomUUID();
		String requestId = null;
		if (basedOn != null) {
			requestId = shiftId + ":" + basedOn + ":" + version;
			jdbc.sql("""
					insert into solve_requests (request_id, shift_id, plan_version, seq, kind, task_id, request, status)
					values (:id, :shiftId, :basedOn, :seq, 'REPLAN', :taskId, '{}', 'OK')
					""")
				.param("id", requestId)
				.param("shiftId", shiftId)
				.param("basedOn", basedOn)
				.param("seq", version)
				.param("taskId", taskId)
				.update();
		}
		jdbc.sql("""
				insert into plans (plan_id, shift_id, version, status, routes, unserved, request_id, activated_at)
				values (:planId, :shiftId, :version, :status, cast(:routes as jsonb), '[]', :requestId,
				        case when :status = 'ACTIVE' then clock_timestamp() end)
				""")
			.param("planId", planId)
			.param("shiftId", shiftId)
			.param("version", version)
			.param("status", status)
			.param("routes", json.writeValueAsString(routes))
			.param("requestId", requestId)
			.update();
		return planId;
	}

	private UUID task(String shiftId, String type) {
		UUID taskId = UUID.randomUUID();
		jdbc.sql("""
				insert into ai_tasks (task_id, type, status, shift_id, created_by)
				values (:taskId, :type, 'WAITING_APPROVAL', :shiftId, 'u-manager')
				""")
			.param("taskId", taskId)
			.param("type", type)
			.param("shiftId", shiftId)
			.update();
		return taskId;
	}

	/** 결정이 끝난(APPROVED) 승인 카드. planId는 카드 본문(계약 ApprovalRequest.planId)에 들어간다. */
	private UUID approval(UUID taskId, String kind, String approver, String truckId, String candidateId, String planId,
			String decidedBy) {
		UUID approvalId = UUID.randomUUID();
		ObjectNode card = json.createObjectNode().put("approvalId", approvalId.toString()).put("kind", kind)
			.put("approver", approver).put("summary", "테스트 카드").put("planId", planId);
		jdbc.sql("""
				insert into approvals (approval_id, task_id, kind, approver_role, truck_id, candidate_id, status, request,
				                       decided_by, decided_at)
				values (:approvalId, :taskId, :kind, :approver, :truckId, :candidateId, 'APPROVED', cast(:request as jsonb),
				        :decidedBy, clock_timestamp())
				""")
			.param("approvalId", approvalId)
			.param("taskId", taskId)
			.param("kind", kind)
			.param("approver", approver)
			.param("truckId", truckId)
			.param("candidateId", candidateId)
			.param("request", json.writeValueAsString(card))
			.param("decidedBy", decidedBy)
			.update();
		return approvalId;
	}

	private String newDriver(String truckId) throws Exception {
		String loginId = "d" + UUID.randomUUID().toString().substring(0, 8);
		jdbc.sql("""
				insert into users (id, login_id, password_hash, name, role, truck_id, status)
				values (:id, :loginId, :hash, '테스트 기사', 'DRIVER', :truckId, 'ACTIVE')
				""")
			.param("id", "u-" + loginId)
			.param("loginId", loginId)
			.param("hash", passwords.encode("test-password"))
			.param("truckId", truckId)
			.update();
		return login(loginId);
	}

	private String status(String planId) {
		return jdbc.sql("select status from plans where plan_id = :p").param("p", planId).query(String.class).single();
	}

	private ArrayNode routes(JsonNode... routes) {
		ArrayNode out = json.createArrayNode();
		for (JsonNode r : routes) {
			out.add(r);
		}
		return out;
	}

	private JsonNode route(String truckId, JsonNode... stops) {
		ObjectNode route = json.createObjectNode().put("truckId", truckId).put("capacity", 15).put("startLoad", 0);
		ArrayNode out = route.putArray("stops");
		for (JsonNode s : stops) {
			((ObjectNode) s).put("seq", out.size() + 1);
			out.add(s);
		}
		return route;
	}

	private JsonNode stop(String stationId, String action, int bikes) {
		return json.createObjectNode().put("stationId", stationId).put("action", action).put("bikes", bikes)
			.put("status", "PENDING");
	}

	// ── HTTP ────────────────────────────

	private HttpResponse<String> activate(String planId, UUID approvalId, UUID taskId) throws Exception {
		return call("POST", "/internal/plans/" + planId + "/activate", null, TOKEN,
				json.writeValueAsString(json.createObjectNode().put("approvalId", approvalId.toString())
					.put("taskId", taskId.toString())));
	}

	private HttpResponse<String> apply(String truckId, UUID approvalId, UUID taskId, String candidateId)
			throws Exception {
		return call("POST", "/internal/routes/" + truckId + "/apply", null, TOKEN,
				json.writeValueAsString(json.createObjectNode().put("approvalId", approvalId.toString())
					.put("taskId", taskId.toString()).put("candidateId", candidateId)));
	}

	private String login(String loginId) throws Exception {
		var res = call("POST", "/api/auth/login", null, null,
				"{\"loginId\":\"" + loginId + "\",\"password\":\"test-password\"}");
		return json.readTree(res.body()).path("accessToken").asString();
	}

	private String code(HttpResponse<String> res) {
		return json.readTree(res.body()).path("code").asString();
	}

	private String detail(HttpResponse<String> res) {
		return json.readTree(res.body()).path("detail").asString();
	}

	private HttpResponse<String> call(String method, String path, String bearer, String internalToken, String body)
			throws Exception {
		var req = HttpRequest.newBuilder(URI.create("http://localhost:" + port + path)).timeout(Duration.ofSeconds(30))
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
