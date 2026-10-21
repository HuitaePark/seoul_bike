package com.seoulbike.backend.auth;

import static org.assertj.core.api.Assertions.assertThat;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.util.UUID;

import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.web.server.LocalServerPort;
import org.springframework.context.annotation.Import;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

import com.seoulbike.backend.TestInfra;

/** 회원가입은 승인 전까지 로그인 불가, 관리자만 승인·발급·비활성화, 기사는 트럭 필수. */
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT, properties = TestInfra.PROPS)
@Import(TestInfra.class)
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
		var req = HttpRequest.newBuilder(URI.create("http://localhost:" + port + path))
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
