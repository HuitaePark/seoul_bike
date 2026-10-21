package com.seoulbike.backend.auth;

import static org.assertj.core.api.Assertions.assertThat;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import java.util.ArrayList;
import java.util.List;
import java.util.UUID;

import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.web.server.LocalServerPort;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

import com.seoulbike.backend.IntegrationTest;

/** 회원가입은 승인 전까지 로그인 불가, 관리자만 승인·발급·비활성화, 기사는 트럭 필수. */
@IntegrationTest
class AccountFlowTest {

	@LocalServerPort
	int port;

	@Autowired
	JsonMapper json;

	private final HttpClient http = HttpClient.newHttpClient();

	@Test
	void signupStaysPendingUntilManagerApprovesWithTruck() throws Exception {
		String loginId = uniqueLoginId();
		var signup = call("POST", "/api/auth/signup", null,
				body(loginId, "password-123", "신규 기사", "requestedRole", "DRIVER"));
		assertThat(signup.statusCode()).isEqualTo(201);
		JsonNode account = json.readTree(signup.body());
		assertThat(account.path("status").asString()).isEqualTo("PENDING");
		assertThat(account.has("passwordHash")).isFalse();

		assertThat(code(login(loginId, "password-123"))).isEqualTo("ACCOUNT_PENDING");
		// 비밀번호가 틀리면 상태를 알려주지 않는다.
		assertThat(code(login(loginId, "wrong-password"))).isEqualTo("INVALID_CREDENTIALS");
		assertThat(code(call("POST", "/api/auth/signup", null,
				body(loginId, "password-123", "중복", "requestedRole", "DRIVER")))).isEqualTo("LOGIN_ID_TAKEN");

		String userId = account.path("id").asString();
		assertThat(call("GET", "/api/admin/users?status=PENDING", token("driver1"), null).statusCode()).isEqualTo(403);

		String manager = token("manager");
		assertThat(call("GET", "/api/admin/users?status=PENDING", manager, null).body()).contains(loginId);
		assertThat(code(call("POST", "/api/admin/users/" + userId + "/approve", manager, "{\"role\":\"DRIVER\"}")))
			.isEqualTo("TRUCK_REQUIRED");
		var approved = call("POST", "/api/admin/users/" + userId + "/approve", manager,
				"{\"role\":\"DRIVER\",\"truckId\":\"truck-9\"}");
		assertThat(approved.statusCode()).isEqualTo(200);
		assertThat(json.readTree(approved.body()).path("status").asString()).isEqualTo("ACTIVE");
		assertThat(code(call("POST", "/api/admin/users/" + userId + "/approve", manager,
				"{\"role\":\"DRIVER\",\"truckId\":\"truck-9\"}"))).isEqualTo("ACCOUNT_NOT_PENDING");

		var loggedIn = login(loginId, "password-123");
		assertThat(loggedIn.statusCode()).isEqualTo(200);
		JsonNode user = json.readTree(loggedIn.body()).path("user");
		assertThat(user.path("role").asString()).isEqualTo("DRIVER");
		assertThat(user.path("truckId").asString()).isEqualTo("truck-9");
	}

	@Test
	void managerIssuesAndDisablesAccountsButNotSelf() throws Exception {
		String manager = token("manager");
		String loginId = uniqueLoginId();
		var created = call("POST", "/api/admin/users", manager,
				body(loginId, "password-123", "책임자 2", "role", "EXECUTIVE"));
		assertThat(created.statusCode()).isEqualTo(201);
		String userId = json.readTree(created.body()).path("id").asString();
		String issued = json.readTree(login(loginId, "password-123").body()).path("accessToken").asString();
		assertThat(call("GET", "/api/me", issued, null).statusCode()).isEqualTo(200);

		assertThat(call("POST", "/api/admin/users/" + userId + "/disable", manager, null).statusCode()).isEqualTo(200);
		assertThat(code(login(loginId, "password-123"))).isEqualTo("ACCOUNT_DISABLED");
		// 이미 받은 토큰도 만료를 기다리지 않고 바로 막힌다.
		assertThat(code(call("GET", "/api/me", issued, null))).isEqualTo("UNAUTHORIZED");
		// 그 옛 토큰이 브라우저에 남아 로그인 요청에 붙어 와도 다른 계정으로 로그인할 수 있다.
		assertThat(call("POST", "/api/auth/login", issued,
				json.writeValueAsString(json.createObjectNode().put("loginId", "driver1").put("password", "test-password")))
			.statusCode()).isEqualTo(200);

		assertThat(code(call("POST", "/api/admin/users/u-manager/disable", manager, null))).isEqualTo("CANNOT_DISABLE_SELF");
		assertThat(code(call("POST", "/api/admin/users", manager,
				body(uniqueLoginId(), "password-123", "트럭 없는 기사", "role", "DRIVER")))).isEqualTo("TRUCK_REQUIRED");
	}

	@Test
	void reopenedAccountGetsBackInOnlyWithANewToken() throws Exception {
		String manager = token("manager");
		String loginId = uniqueLoginId();
		String userId = json.readTree(call("POST", "/api/admin/users", manager,
				body(loginId, "password-123", "책임자 4", "role", "EXECUTIVE")).body()).path("id").asString();
		String enable = "/api/admin/users/" + userId + "/enable";
		String before = json.readTree(login(loginId, "password-123").body()).path("accessToken").asString();
		assertThat(code(call("POST", enable, manager, null))).isEqualTo("ACCOUNT_NOT_DISABLED");

		call("POST", "/api/admin/users/" + userId + "/disable", manager, null);
		assertThat(call("GET", "/api/me", before, null).statusCode()).isEqualTo(401);
		var reopened = call("POST", enable, manager, null);
		assertThat(reopened.statusCode()).isEqualTo(200);
		assertThat(json.readTree(reopened.body()).path("status").asString()).isEqualTo("ACTIVE");
		assertThat(json.readTree(reopened.body()).path("role").asString()).isEqualTo("EXECUTIVE");

		// 비활성화 전에 받은 토큰은 다시 열어도 쓰지 못한다. 새로 로그인해야 한다.
		assertThat(call("GET", "/api/me", before, null).statusCode()).isEqualTo(401);
		String after = json.readTree(login(loginId, "password-123").body()).path("accessToken").asString();
		assertThat(call("GET", "/api/me", after, null).statusCode()).isEqualTo(200);
	}

	@Test
	void rejectedSignupIsNotReopenedIntoTheRoleItAskedFor() throws Exception {
		String manager = token("manager");
		String loginId = uniqueLoginId();
		String signupId = json.readTree(call("POST", "/api/auth/signup", null,
				body(loginId, "password-123", "거절할 신청", "requestedRole", "MANAGER")).body()).path("id").asString();
		String enable = "/api/admin/users/" + signupId + "/enable";
		// 승인 대기도 이 길로는 승인되지 않는다. 역할·트럭은 승인에서 정한다.
		assertThat(code(call("POST", enable, manager, null))).isEqualTo("ACCOUNT_NOT_DISABLED");

		call("POST", "/api/admin/users/" + signupId + "/disable", manager, null);
		assertThat(code(call("POST", enable, manager, null))).isEqualTo("SIGNUP_REJECTED");
		assertThat(code(login(loginId, "password-123"))).isEqualTo("ACCOUNT_DISABLED");

		// 화면은 approvedAt으로 다시 열 수 있는 계정과 거절한 신청을 구분한다.
		var disabled = json.readTree(call("GET", "/api/admin/users?status=DISABLED", manager, null).body());
		var rejected = disabled.valueStream().filter(a -> a.path("id").asString().equals(signupId)).findFirst().orElseThrow();
		assertThat(rejected.path("approvedAt").isString()).isFalse();
	}

	@Test
	void onlyManagersReopenAccounts() throws Exception {
		String enable = "/api/admin/users/u-no-such-user/enable";
		assertThat(call("POST", enable, token("driver1"), null).statusCode()).isEqualTo(403);
		assertThat(call("POST", enable, token("executive"), null).statusCode()).isEqualTo(403);
		assertThat(call("POST", enable, token("manager"), null).statusCode()).isEqualTo(404);
	}

	@Test
	void repeatedWrongPasswordsBlockOnlyThatLoginIdFromThatIp() throws Exception {
		String manager = token("manager");
		String loginId = uniqueLoginId();
		call("POST", "/api/admin/users", manager, body(loginId, "password-123", "책임자 3", "role", "EXECUTIVE"));

		// TestInfra: 3번까지는 그냥 틀렸다고 한다.
		for (int i = 0; i < 3; i++) {
			assertThat(code(login(loginId, "wrong-password"))).isEqualTo("INVALID_CREDENTIALS");
		}
		// 그 뒤로는 맞는 비밀번호도 받지 않는다. 없는 아이디도 똑같이 센다.
		var blocked = login(loginId, "password-123");
		assertThat(blocked.statusCode()).isEqualTo(429);
		assertThat(code(blocked)).isEqualTo("TOO_MANY_LOGIN_FAILURES");
		String ghost = uniqueLoginId();
		for (int i = 0; i < 3; i++) {
			login(ghost, "whatever-123");
		}
		assertThat(code(login(ghost, "whatever-123"))).isEqualTo("TOO_MANY_LOGIN_FAILURES");
		// 다른 아이디는 영향이 없다.
		assertThat(login("driver1", "test-password").statusCode()).isEqualTo(200);
		// 막힌 건 틀린 곳(IP)뿐이다. 다른 IP에서는 같은 아이디로 바로 로그인한다 (남이 일부러 틀려 계정을 막지 못하게).
		// 테스트는 127.0.0.1(믿는 프록시)에서 보내므로 X-Forwarded-For가 접속 IP가 된다.
		assertThat(loginFrom("203.0.113.7", loginId, "password-123").statusCode()).isEqualTo(200);
		assertThat(code(loginFrom("203.0.113.8", loginId, "wrong-password"))).isEqualTo("INVALID_CREDENTIALS");
	}

	private HttpResponse<String> loginFrom(String ip, String loginId, String password) throws Exception {
		var req = HttpRequest.newBuilder(URI.create("http://localhost:" + port + "/api/auth/login")).timeout(Duration.ofSeconds(30))
			.header("Content-Type", "application/json")
			.header("X-Forwarded-For", ip)
			.POST(HttpRequest.BodyPublishers.ofString(
					json.writeValueAsString(json.createObjectNode().put("loginId", loginId).put("password", password))))
			.build();
		return http.send(req, HttpResponse.BodyHandlers.ofString());
	}

	@Test
	void signupsStopWhileTooManyArePending() throws Exception {
		String manager = token("manager");
		List<String> created = new ArrayList<>();
		try {
			// TestInfra: 승인 대기 5건까지. 다른 테스트가 남긴 대기 건이 있어도 5건까지 채운다.
			int pending = json.readTree(call("GET", "/api/admin/users?status=PENDING", manager, null).body()).size();
			for (int i = pending; i < 5; i++) {
				var ok = call("POST", "/api/auth/signup", null, body(uniqueLoginId(), "password-123", "대기", "requestedRole", "DRIVER"));
				assertThat(ok.statusCode()).isEqualTo(201);
				created.add(json.readTree(ok.body()).path("id").asString());
			}
			var full = call("POST", "/api/auth/signup", null, body(uniqueLoginId(), "password-123", "넘침", "requestedRole", "DRIVER"));
			assertThat(full.statusCode()).isEqualTo(429);
			assertThat(code(full)).isEqualTo("TOO_MANY_PENDING_SIGNUPS");
		}
		finally {
			// 거절(비활성화)하면 대기에서 빠진다. 뒤 테스트의 가입을 막지 않게 치운다.
			for (String id : created) {
				call("POST", "/api/admin/users/" + id + "/disable", manager, null);
			}
		}
	}

	@Test
	void signupRejectsMalformedInput() throws Exception {
		var badLoginId = call("POST", "/api/auth/signup", null, body("AB", "password-123", "x", "requestedRole", "DRIVER"));
		assertThat(badLoginId.statusCode()).isEqualTo(400);
		JsonNode problem = json.readTree(badLoginId.body());
		assertThat(problem.path("code").asString()).isEqualTo("INVALID_REQUEST");
		assertThat(problem.path("detail").asString()).startsWith("loginId:");
		assertThat(call("POST", "/api/auth/signup", null, body(uniqueLoginId(), "short", "x", "requestedRole", "DRIVER"))
			.statusCode()).isEqualTo(400);
	}

	private static String uniqueLoginId() {
		return "user-" + UUID.randomUUID().toString().substring(0, 8);
	}

	private String body(String loginId, String password, String name, String roleField, String role) {
		return json.writeValueAsString(json.createObjectNode()
			.put("loginId", loginId)
			.put("password", password)
			.put("name", name)
			.put(roleField, role));
	}

	private String token(String loginId) throws Exception {
		return json.readTree(login(loginId, "test-password").body()).path("accessToken").asString();
	}

	private HttpResponse<String> login(String loginId, String password) throws Exception {
		return call("POST", "/api/auth/login", null,
				json.writeValueAsString(json.createObjectNode().put("loginId", loginId).put("password", password)));
	}

	private String code(HttpResponse<String> res) {
		return json.readTree(res.body()).path("code").asString();
	}

	private HttpResponse<String> call(String method, String path, String token, String body) throws Exception {
		var req = HttpRequest.newBuilder(URI.create("http://localhost:" + port + path)).timeout(Duration.ofSeconds(30))
			.method(method, body == null ? HttpRequest.BodyPublishers.noBody() : HttpRequest.BodyPublishers.ofString(body));
		if (body != null) {
			req.header("Content-Type", "application/json");
		}
		if (token != null) {
			req.header("Authorization", "Bearer " + token);
		}
		return http.send(req.build(), HttpResponse.BodyHandlers.ofString());
	}
}
