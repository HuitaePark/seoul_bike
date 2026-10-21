package com.seoulbike.backend.plan;

import static org.assertj.core.api.Assertions.assertThat;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import java.time.LocalDate;
import java.util.ArrayList;
import java.util.List;
import java.util.UUID;
import java.util.concurrent.ThreadLocalRandom;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.web.server.LocalServerPort;
import org.springframework.jdbc.core.simple.JdbcClient;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ArrayNode;
import tools.jackson.databind.node.ObjectNode;

import com.seoulbike.backend.IntegrationTest;

/**
 * GET /internal/shifts/{shiftId}/trucks (business-mcp get_truck_status). 기준 정보(구역·대여소·트럭)는 시작할 때 올라간 실제 값,
 * 활성 계획은 행을 넣는다. 근무조는 테스트마다 S02의 서로 다른 날짜다.
 */
@IntegrationTest
class TruckStatusApiTest {

	private static final String TOKEN = "test-internal-token";
	private static final String REGION = "S02";

	@LocalServerPort
	int port;

	@Autowired
	JsonMapper json;

	@Autowired
	JdbcClient jdbc;

	private final HttpClient http = HttpClient.newHttpClient();

	private List<String> st;

	private List<String> trucks;

	private List<Double> depot;

	@BeforeEach
	void setUp() {
		st = jdbc.sql("select station_id from ref.stations where region_id = :r and active order by station_id limit 4")
			.param("r", REGION)
			.query(String.class)
			.list();
		trucks = jdbc.sql("select truck_id from ref.trucks where region_id = :r and active order by truck_id")
			.param("r", REGION)
			.query(String.class)
			.list();
		assertThat(trucks).hasSizeGreaterThanOrEqualTo(2);
		depot = jdbc.sql("select depot_lat, depot_lon from ref.regions where region_id = :r").param("r", REGION)
			.query((rs, i) -> List.of(rs.getDouble(1), rs.getDouble(2))).single();
	}

	@Test
	void 활성_계획이_있으면_마지막으로_끝낸_정류장의_위치와_적재와_남은_정류장을_준다() throws Exception {
		String day = futureDay();
		String shift = REGION + ":" + day + ":PM";
		String t1 = trucks.get(0), t2 = trucks.get(1);
		ArrayNode routes = json.createArrayNode();
		routes.add(route(t1, 2, stop(1, st.get(0), "PICKUP", 3, 5, "DONE"), stop(2, st.get(1), "PICKUP", 4, 9, "DONE"),
				stop(3, st.get(2), "DROPOFF", 9, 0, "PENDING"), stop(4, st.get(3), "DROPOFF", 1, 0, "SKIPPED")));
		routes.add(route(t2, 1, stop(1, st.get(3), "DROPOFF", 1, 0, "PENDING")));
		insertActive(shift, routes);

		var res = get("/internal/shifts/" + shift + "/trucks", TOKEN);

		assertThat(res.statusCode()).as(res.body()).isEqualTo(200);
		JsonNode body = json.readTree(res.body());
		assertThat(ids(body, "truckId")).containsExactly(t1, t2);
		JsonNode first = body.get(0);
		assertThat(first.path("capacity").asInt()).isEqualTo(15);
		assertThat(first.path("load").asInt()).isEqualTo(9);
		var at = position(st.get(1));
		assertThat(first.path("lat").asDouble()).isEqualTo(at.get(0));
		assertThat(first.path("lon").asDouble()).isEqualTo(at.get(1));
		assertThat(first.path("atStationId").asString()).isEqualTo(st.get(1));
		assertThat(first.path("shiftEnd").asString()).isEqualTo(day + "T16:00:00+09:00");
		assertThat(ids(first.path("remainingStops"), "stationId")).containsExactly(st.get(2));
		assertThat(first.path("remainingStops").get(0)).isEqualTo(routes.get(0).path("stops").get(2));
		assertThat(first.has("remainingStationIds")).isFalse();
		// 아직 끝낸 정류장이 없는 트럭은 차고지에서 출발 적재 그대로다.
		JsonNode second = body.get(1);
		assertThat(second.path("load").asInt()).isEqualTo(1);
		assertThat(second.path("lat").asDouble()).isEqualTo(depot.get(0));
		assertThat(second.path("lon").asDouble()).isEqualTo(depot.get(1));
		assertThat(second.path("atStationId").isTextual()).isFalse();

		var one = get("/internal/shifts/" + shift + "/trucks?truckId=" + t2, TOKEN);
		assertThat(ids(json.readTree(one.body()), "truckId")).containsExactly(t2);
		var unknown = get("/internal/shifts/" + shift + "/trucks?truckId=TR-99", TOKEN);
		assertThat(unknown.statusCode()).isEqualTo(404);
		assertThat(json.readTree(unknown.body()).path("code").asString()).isEqualTo("TRUCK_NOT_FOUND");
		assertThat(ids(json.readTree(unknown.body()).path("suggestions"), null)).containsExactly(t1, t2);
	}

	@Test
	void 활성_계획이_없으면_구역_트럭이_차고지에_빈_채로_있다() throws Exception {
		String day = futureDay();
		var res = get("/internal/shifts/" + REGION + ":" + day + ":EVE/trucks", TOKEN);

		assertThat(res.statusCode()).as(res.body()).isEqualTo(200);
		JsonNode body = json.readTree(res.body());
		assertThat(ids(body, "truckId")).containsExactlyElementsOf(trucks);
		for (JsonNode t : body) {
			assertThat(t.path("load").asInt()).isZero();
			assertThat(t.path("lat").asDouble()).isEqualTo(depot.get(0));
			assertThat(t.path("lon").asDouble()).isEqualTo(depot.get(1));
			assertThat(t.path("shiftEnd").asString()).isEqualTo(day + "T21:00:00+09:00");
			assertThat(t.path("remainingStops")).isEmpty();
		}
	}

	@Test
	void 근무조_형식과_구역과_서비스_토큰을_검사한다() throws Exception {
		var malformed = get("/internal/shifts/S02-2026-10-21-AM/trucks", TOKEN);
		assertThat(malformed.statusCode()).isEqualTo(400);
		assertThat(json.readTree(malformed.body()).path("code").asString()).isEqualTo("INVALID_REQUEST");
		var unknownRegion = get("/internal/shifts/N09:" + futureDay() + ":AM/trucks", TOKEN);
		assertThat(unknownRegion.statusCode()).isEqualTo(404);
		assertThat(json.readTree(unknownRegion.body()).path("code").asString()).isEqualTo("SHIFT_NOT_FOUND");
		assertThat(get("/internal/shifts/" + REGION + ":" + futureDay() + ":AM/trucks", null).statusCode()).isEqualTo(401);
		assertThat(get("/internal/shifts/" + REGION + ":" + futureDay() + ":AM/trucks", "wrong").statusCode())
			.isEqualTo(401);
	}

	/** 관제 지도(GET /api/shifts/{shiftId}/trucks)는 에이전트 도구와 같은 값을 본다. 모든 트럭이 있어 기사는 못 본다. */
	@Test
	void 화면도_같은_트럭_상태를_보고_기사는_볼_수_없다() throws Exception {
		String shift = REGION + ":" + futureDay() + ":AM";
		insertActive(shift, json.createArrayNode().add(route(trucks.get(0), 3,
				stop(1, st.get(0), "PICKUP", 4, 7, "DONE"), stop(2, st.get(1), "DROPOFF", 7, 0, "PENDING"))));
		JsonNode internal = json.readTree(get("/internal/shifts/" + shift + "/trucks", TOKEN).body());
		assertThat(internal.get(0).path("atStationId").asString()).isEqualTo(st.get(0));

		String path = "/api/shifts/" + shift + "/trucks";
		for (String who : List.of("manager", "executive")) {
			var res = getAs(path, login(who));
			assertThat(res.statusCode()).as(who + " " + res.body()).isEqualTo(200);
			assertThat(json.readTree(res.body())).isEqualTo(internal);
		}
		assertThat(getAs(path, login("driver1")).statusCode()).isEqualTo(403);
		assertThat(getAs(path, null).statusCode()).isEqualTo(401);
		String manager = login("manager");
		assertThat(getAs("/api/shifts/S02-2026-10-21-AM/trucks", manager).statusCode()).isEqualTo(400);
		var unknownRegion = getAs("/api/shifts/N09:" + futureDay() + ":AM/trucks", manager);
		assertThat(unknownRegion.statusCode()).isEqualTo(404);
		assertThat(json.readTree(unknownRegion.body()).path("code").asString()).isEqualTo("SHIFT_NOT_FOUND");
	}

	private static String futureDay() {
		return LocalDate.of(2027, 1, 1).plusDays(ThreadLocalRandom.current().nextInt(100_000)).toString();
	}

	private List<Double> position(String stationId) {
		return jdbc.sql("select lat, lon from ref.stations where station_id = :id").param("id", stationId)
			.query((rs, i) -> List.of(rs.getDouble(1), rs.getDouble(2))).single();
	}

	private void insertActive(String shiftId, ArrayNode routes) {
		jdbc.sql("""
				insert into plans (plan_id, shift_id, version, status, routes, unserved, activated_at)
				values (:id, :shiftId, 1, 'ACTIVE', cast(:routes as jsonb), '[]', clock_timestamp())
				""")
			.param("id", "PL-" + UUID.randomUUID())
			.param("shiftId", shiftId)
			.param("routes", json.writeValueAsString(routes))
			.update();
	}

	private ObjectNode route(String truckId, int startLoad, ObjectNode... stops) {
		ObjectNode route = json.createObjectNode().put("truckId", truckId).put("capacity", 15).put("startLoad", startLoad);
		ArrayNode out = route.putArray("stops");
		for (ObjectNode s : stops) {
			out.add(s);
		}
		return route;
	}

	private ObjectNode stop(int seq, String stationId, String action, int bikes, int loadAfter, String status) {
		return json.createObjectNode().put("seq", seq).put("stationId", stationId).put("action", action)
			.put("bikes", bikes).put("loadAfter", loadAfter).put("eta", "2027-01-01T13:10:00+09:00").put("status", status);
	}

	private HttpResponse<String> get(String path, String token) throws Exception {
		var req = HttpRequest.newBuilder(URI.create("http://localhost:" + port + path)).timeout(Duration.ofSeconds(30)).GET();
		if (token != null) {
			req.header("X-Internal-Token", token);
		}
		return http.send(req.build(), HttpResponse.BodyHandlers.ofString());
	}

	private HttpResponse<String> getAs(String path, String bearer) throws Exception {
		var req = HttpRequest.newBuilder(URI.create("http://localhost:" + port + path)).timeout(Duration.ofSeconds(30)).GET();
		if (bearer != null) {
			req.header("Authorization", "Bearer " + bearer);
		}
		return http.send(req.build(), HttpResponse.BodyHandlers.ofString());
	}

	private String login(String loginId) throws Exception {
		var req = HttpRequest.newBuilder(URI.create("http://localhost:" + port + "/api/auth/login"))
			.header("Content-Type", "application/json")
			.POST(HttpRequest.BodyPublishers.ofString("{\"loginId\":\"" + loginId + "\",\"password\":\"test-password\"}"))
			.build();
		return json.readTree(http.send(req, HttpResponse.BodyHandlers.ofString()).body()).path("accessToken").asString();
	}

	private static List<String> ids(JsonNode array, String field) {
		List<String> out = new ArrayList<>();
		array.forEach(n -> out.add(field == null ? n.asString() : n.path(field).asString()));
		return out;
	}
}
