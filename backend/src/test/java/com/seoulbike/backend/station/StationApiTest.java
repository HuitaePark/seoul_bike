package com.seoulbike.backend.station;

import static org.assertj.core.api.Assertions.assertThat;

import java.net.URI;
import java.net.URLEncoder;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.time.Instant;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.ArrayList;
import java.util.List;
import java.util.UUID;
import java.util.stream.IntStream;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.web.server.LocalServerPort;
import org.springframework.jdbc.core.simple.JdbcClient;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

import com.seoulbike.backend.IntegrationTest;

/**
 * 현황 지도(openapi)와 대여소·예측 도구(internal-api). 기준 정보는 시작할 때 올라간 실제 대여소(ref)를 쓰고, 실시간 재고·예측 표는
 * 운영에서도 6번이 채우므로 여기서 행을 넣는다. 두 표는 이 테스트만 쓰므로 테스트마다 비운다.
 */
@IntegrationTest
class StationApiTest {

	private static final String TOKEN = "test-internal-token";
	private static final String REGION = "S05";

	@LocalServerPort
	int port;

	@Autowired
	JsonMapper json;

	@Autowired
	JdbcClient jdbc;

	private final HttpClient http = HttpClient.newHttpClient();

	/** S05의 운영 중인 대여소 앞 8곳. */
	private List<String> st;

	@BeforeEach
	void clean() {
		jdbc.sql("delete from live.station_status").update();
		jdbc.sql("delete from forecast.station_forecasts").update();
		st = jdbc.sql("select station_id from ref.stations where region_id = :r and active order by station_id limit 8")
			.param("r", REGION)
			.query(String.class)
			.list();
	}

	@Test
	void 현황_지도는_대여소마다_가장_최근_관측을_쓰고_관측이_없거나_운영하지_않는_대여소는_뺀다() throws Exception {
		String latest = st.get(0), over = st.get(1), forecast = st.get(2), inactive = st.get(3);
		observe(latest, 9, 10, 60);
		observe(latest, 1, 10, 5);
		observe(over, 12, 10, 5);
		observe(forecast, 5, 10, 5);
		observe(inactive, 0, 10, 5);
		// 진행 중인 3시간 구간(30분 전 시작)을 쓰고, 끝난 구간(5시간 전 시작)은 쓰지 않는다.
		forecastNow(forecast, 30, 3, 4, 0);
		forecastNow(forecast, 300, 3, 0, 20);
		jdbc.sql("update ref.stations set active = false where station_id = :id").param("id", inactive).update();
		JsonNode body;
		try {
			var res = call("/api/regions/" + REGION + "/stations", login("manager"), null);
			assertThat(res.statusCode()).isEqualTo(200);
			body = json.readTree(res.body());
		}
		finally {
			jdbc.sql("update ref.stations set active = true where station_id = :id").param("id", inactive).update();
		}

		assertThat(ids(body)).containsExactly(latest, over, forecast);
		JsonNode first = body.get(0);
		assertThat(first.path("bikes").asInt()).isEqualTo(1);
		assertThat(first.path("risk").asString()).isEqualTo("EMPTY");
		assertThat(first.path("forecastBikes").isNull()).isTrue();
		var ref = jdbc.sql("select name, role from ref.stations where station_id = :id").param("id", latest)
			.query((rs, i) -> List.of(rs.getString("name"), rs.getString("role"))).single();
		assertThat(first.path("name").asString()).isEqualTo(ref.get(0));
		assertThat(first.path("role").asString()).isEqualTo(ref.get(1));
		assertThat(first.path("docks").asInt()).isEqualTo(10);
		assertThat(first.has("lat") && first.has("lon") && first.has("elevationM") && first.has("observedAt")).isTrue();
		assertThat(body.get(1).path("risk").asString()).isEqualTo("OVERFLOW");
		assertThat(body.get(2).path("forecastBikes").asInt()).isEqualTo(1);
		assertThat(body.get(2).path("risk").asString()).isEqualTo("EMPTY");

		// 1시간 예측이 없으면 현재 재고(5대)로만 본다.
		JsonNode oneHour = json.readTree(call("/api/regions/" + REGION + "/stations?horizonH=1", login("manager"), null).body());
		assertThat(oneHour.get(2).path("forecastBikes").isNull()).isTrue();
		assertThat(oneHour.get(2).path("risk").asString()).isEqualTo("NONE");
	}

	@Test
	void 현황_지도는_로그인한_모든_역할이_보고_구역과_예측_구간을_검사한다() throws Exception {
		observe(st.get(0), 5, 10, 5);
		for (String user : List.of("manager", "driver1", "executive")) {
			var res = call("/api/regions/" + REGION + "/stations", login(user), null);
			assertThat(res.statusCode()).as(user).isEqualTo(200);
			assertThat(json.readTree(res.body())).hasSize(1);
		}
		assertThat(call("/api/regions/" + REGION + "/stations", null, null).statusCode()).isEqualTo(401);
		String manager = login("manager");
		var unknown = call("/api/regions/X99/stations", manager, null);
		assertThat(unknown.statusCode()).isEqualTo(404);
		assertThat(code(unknown)).isEqualTo("REGION_NOT_FOUND");
		assertThat(json.readTree(unknown.body()).path("suggestions")).hasSize(17);
		assertThat(code(call("/api/regions/" + REGION + "/stations?horizonH=6", manager, null)))
			.isEqualTo("INVALID_REQUEST");
		assertThat(call("/api/regions/" + REGION + "/stations?horizonH=0", manager, null).statusCode()).isEqualTo(400);
		// 관측이 하나도 없는 구역은 빈 목록이다 (404가 아니다).
		assertThat(json.readTree(call("/api/regions/N01/stations", manager, null).body())).isEmpty();
	}

	@Test
	void 위험_대여소는_위험한_것만_우선순위_내림차순으로_준다() throws Exception {
		String empty = st.get(0), over = st.get(1), low = st.get(2), later = st.get(3), hourly = st.get(4),
				fine = st.get(5);
		observe(empty, 0, 10, 5); // 지금 0대: 3대 배치, 2 × 3 = 6
		observe(over, 13, 10, 5); // 3대 넘음: 3대 회수, 1 × 3 = 3
		observe(low, 2, 10, 5); // 지금 2대: 1대 배치, 2 × 1 = 2
		observe(later, 8, 10, 5); // 3시간 뒤 2대: 2 × 1 / 4 = 0.5
		forecastNow(later, 0, 3, 6, 0);
		observe(hourly, 5, 10, 5); // 1시간 뒤 1대, 끝은 5대: 2 × 2 / 2 = 2
		forecastNow(hourly, 0, 3, 4, 4, "{4,0,0}", "{0,2,2}");
		observe(fine, 5, 10, 5);

		var res = call("/internal/stations/risk?regionId=" + REGION, null, TOKEN);
		assertThat(res.statusCode()).isEqualTo(200);
		JsonNode body = json.readTree(res.body());
		// 같은 우선순위(2)는 대여소 ID 순.
		List<String> tie = new ArrayList<>(List.of(low, hourly));
		tie.sort(String::compareTo);
		assertThat(ids(body)).containsExactly(empty, over, tie.get(0), tie.get(1), later);
		JsonNode top = body.get(0);
		assertThat(top.path("risk").asString()).isEqualTo("EMPTY");
		assertThat(top.path("neededBikes").asInt()).isEqualTo(-3);
		assertThat(top.path("priority").asDouble()).isEqualTo(6.0);
		assertThat(top.path("emptyAt").isNull()).isFalse();
		assertThat(top.path("reasons").get(0).asString()).isEqualTo("현재 재고 0대, 거치대 10개");
		assertThat(body.get(1).path("neededBikes").asInt()).isEqualTo(3);
		JsonNode h = body.get(ids(body).indexOf(hourly));
		assertThat(h.path("neededBikes").asInt()).isEqualTo(-2);
		assertThat(h.path("forecastBikes").asInt()).isEqualTo(5);

		assertThat(ids(json.readTree(call("/internal/stations/risk?regionId=" + REGION + "&limit=2", null, TOKEN).body())))
			.containsExactly(empty, over);
		assertThat(call("/internal/stations/risk?regionId=" + REGION + "&limit=201", null, TOKEN).statusCode())
			.isEqualTo(400);
		assertThat(call("/internal/stations/risk?regionId=" + REGION + "&horizonH=6", null, TOKEN).statusCode())
			.isEqualTo(400);
		assertThat(call("/internal/stations/risk", null, TOKEN).statusCode()).isEqualTo(400);
		assertThat(code(call("/internal/stations/risk?regionId=N99", null, TOKEN))).isEqualTo("REGION_NOT_FOUND");
		// 사용자 JWT로는 열리지 않는다.
		assertThat(call("/internal/stations/risk?regionId=" + REGION, login("manager"), null).statusCode()).isEqualTo(401);
	}

	@Test
	void 대여소_상세는_구와_최근_현장_보고_5건을_주고_없는_대여소에는_고칠_후보를_준다() throws Exception {
		String id = st.get(0);
		observe(id, 4, 12, 5);
		for (int i = 0; i < 6; i++) {
			report(id, i, 60 - i);
		}

		var res = call("/internal/stations/" + id, null, TOKEN);
		assertThat(res.statusCode()).isEqualTo(200);
		JsonNode body = json.readTree(res.body());
		assertThat(body.path("bikes").asInt()).isEqualTo(4);
		assertThat(body.path("docks").asInt()).isEqualTo(12);
		assertThat(body.path("risk").asString()).isEqualTo("NONE");
		assertThat(body.path("district").asString()).isEqualTo(
				jdbc.sql("select district from ref.stations where station_id = :id").param("id", id).query(String.class).single());
		JsonNode reports = body.path("recentReports");
		assertThat(reports).hasSize(5);
		assertThat(reports.get(0).path("actualBikes").asInt()).isEqualTo(5); // 가장 최근 것부터
		assertThat(reports.get(0).path("driver").asString()).isEqualTo("truck-1 기사");

		var unknown = call("/internal/stations/ST-99999999", null, TOKEN);
		assertThat(unknown.statusCode()).isEqualTo(404);
		assertThat(code(unknown)).isEqualTo("STATION_NOT_FOUND");
		// 대여소번호를 넣으면 그 번호의 대여소 ID를 알려 준다 (번호 102 = ST-4, ADR 0001).
		var byNo = call("/internal/stations/102", null, TOKEN);
		assertThat(code(byNo)).isEqualTo("STATION_NOT_FOUND");
		assertThat(json.readTree(byNo.body()).path("suggestions").get(0).asString()).isEqualTo("ST-4");

		String unobserved = st.get(1);
		assertThat(code(call("/internal/stations/" + unobserved, null, TOKEN))).isEqualTo("STATION_STATUS_NOT_FOUND");

		jdbc.sql("update ref.stations set active = false where station_id = :id").param("id", id).update();
		try {
			var inactive = call("/internal/stations/" + id, null, TOKEN);
			assertThat(inactive.statusCode()).isEqualTo(404);
			assertThat(detail(inactive)).contains("운영하지 않는 대여소");
			JsonNode near = json.readTree(inactive.body()).path("suggestions");
			assertThat(near).hasSize(3);
			assertThat(near.toString()).doesNotContain("\"" + id + "\"");
		}
		finally {
			jdbc.sql("update ref.stations set active = true where station_id = :id").param("id", id).update();
		}
	}

	@Test
	void 예측은_start와_horizonH가_같은_행을_요청한_순서대로_준다() throws Exception {
		String a = st.get(0), b = st.get(1), none = st.get(2);
		Instant start = Instant.parse("2026-10-21T22:00:00Z"); // 2026-10-22 07:00 KST
		forecast("S05:2026-10-22:AM", a, start, 3, "6.5", "2", "{2,2.5,2}", "{1,0,1}", 120);
		// 같은 start·horizonH가 둘이면 나중에 넣은 것.
		forecast("S05:2026-10-22:AM:rerun", a, start, 3, "7", "2", null, null, 10);
		forecast("S05:2026-10-22:AM", a, start, 1, "1", "0", "{1}", "{0}", 120);
		forecast("S05:2026-10-22:AM", b, start, 3, "3", "4", null, null, 120);

		String q = "/internal/forecasts?stationIds=" + b + "," + a + "," + none + "&start=";
		var res = call(q + start, null, TOKEN);
		assertThat(res.statusCode()).isEqualTo(200);
		JsonNode body = json.readTree(res.body());
		assertThat(ids(body)).containsExactly(b, a);
		assertThat(body.get(0).path("horizonH").asInt()).isEqualTo(3);
		assertThat(body.get(0).has("rentalsH")).isFalse();
		assertThat(body.get(1).path("rentals").asDouble()).isEqualTo(7.0);
		assertThat(body.get(1).path("modelVersion").asString()).isEqualTo("test-model");

		// 같은 시각을 +09:00으로 보내도 같다. 시간별 값은 배열로 나온다.
		String kst = URLEncoder.encode("2026-10-22T07:00:00+09:00", StandardCharsets.UTF_8);
		JsonNode oneHour = json.readTree(call(q + kst + "&horizonH=1", null, TOKEN).body());
		assertThat(ids(oneHour)).containsExactly(a);
		assertThat(oneHour.get(0).path("rentalsH").get(0).asDouble()).isEqualTo(1.0);
		JsonNode other = json.readTree(call(q + start.plus(Duration.ofHours(6)), null, TOKEN).body());
		assertThat(other).isEmpty();

		var unknown = call("/internal/forecasts?stationIds=" + a + ",ST-99999999&start=" + start, null, TOKEN);
		assertThat(unknown.statusCode()).isEqualTo(404);
		assertThat(code(unknown)).isEqualTo("STATION_NOT_FOUND");
		assertThat(detail(unknown)).contains("ST-99999999").doesNotContain("'" + a + "'");
		String tooMany = String.join(",", IntStream.range(0, 201).mapToObj(i -> "ST-" + i).toList());
		assertThat(call("/internal/forecasts?stationIds=" + tooMany + "&start=" + start, null, TOKEN).statusCode())
			.isEqualTo(400);
		assertThat(call(q + start + "&horizonH=6", null, TOKEN).statusCode()).isEqualTo(400);
		assertThat(call("/internal/forecasts?stationIds=" + a, null, TOKEN).statusCode()).isEqualTo(400);
	}

	// ── 데이터 ────────────────────────────

	private void observe(String stationId, int bikes, int docks, int minutesAgo) {
		jdbc.sql("""
				insert into live.station_status (observed_at, station_id, bikes, docks)
				values (now() - make_interval(mins => :ago), :id, :bikes, :docks)
				""")
			.param("ago", minutesAgo)
			.param("id", stationId)
			.param("bikes", bikes)
			.param("docks", docks)
			.update();
	}

	/** 지금부터 startedMinutesAgo분 전에 시작한 예측 구간. */
	private void forecastNow(String stationId, int startedMinutesAgo, int horizonH, double rentals, double returns) {
		forecastNow(stationId, startedMinutesAgo, horizonH, rentals, returns, null, null);
	}

	private void forecastNow(String stationId, int startedMinutesAgo, int horizonH, double rentals, double returns,
			String rentalsH, String returnsH) {
		Instant start = jdbc.sql("select now() - make_interval(mins => :ago)").param("ago", startedMinutesAgo)
			.query(OffsetDateTime.class).single().toInstant();
		forecast("S05:now:" + startedMinutesAgo, stationId, start, horizonH, Double.toString(rentals),
				Double.toString(returns), rentalsH, returnsH, 0);
	}

	private void forecast(String shiftId, String stationId, Instant start, int horizonH, String rentals,
			String returns, String rentalsH, String returnsH, int createdMinutesAgo) {
		jdbc.sql("""
				insert into forecast.station_forecasts (shift_id, station_id, start, horizon_h, rentals, returns,
				                                        rentals_h, returns_h, model_version, created_at)
				values (:shift, :id, :start, :h, cast(:rentals as numeric), cast(:returns as numeric),
				        cast(:rentalsH as numeric[]), cast(:returnsH as numeric[]), 'test-model',
				        now() - make_interval(mins => :ago))
				""")
			.param("shift", shiftId)
			.param("id", stationId)
			.param("start", OffsetDateTime.ofInstant(start, ZoneOffset.UTC))
			.param("h", horizonH)
			.param("rentals", rentals)
			.param("returns", returns)
			.param("rentalsH", rentalsH)
			.param("returnsH", returnsH)
			.param("ago", createdMinutesAgo)
			.update();
	}

	private void report(String stationId, int actualBikes, int minutesAgo) {
		jdbc.sql("""
				insert into field_reports (report_id, shift_id, truck_id, station_id, actual_bikes, reported_by, reported_at)
				values (:id, 'S05:2026-10-22:AM', 'truck-1', :station, :bikes, 'u-driver-1',
				        now() - make_interval(mins => :ago))
				""")
			.param("id", UUID.randomUUID())
			.param("station", stationId)
			.param("bikes", actualBikes)
			.param("ago", minutesAgo)
			.update();
	}

	// ── HTTP ────────────────────────────

	private List<String> ids(JsonNode array) {
		List<String> out = new ArrayList<>();
		array.forEach(n -> out.add(n.path("stationId").asString()));
		return out;
	}

	private String login(String loginId) throws Exception {
		var req = HttpRequest.newBuilder(URI.create("http://localhost:" + port + "/api/auth/login"))
			.header("Content-Type", "application/json")
			.POST(HttpRequest.BodyPublishers.ofString("{\"loginId\":\"" + loginId + "\",\"password\":\"test-password\"}"))
			.build();
		return json.readTree(http.send(req, HttpResponse.BodyHandlers.ofString()).body()).path("accessToken").asString();
	}

	private String code(HttpResponse<String> res) {
		return json.readTree(res.body()).path("code").asString();
	}

	private String detail(HttpResponse<String> res) {
		return json.readTree(res.body()).path("detail").asString();
	}

	private HttpResponse<String> call(String path, String bearer, String internalToken) throws Exception {
		var req = HttpRequest.newBuilder(URI.create("http://localhost:" + port + path)).timeout(Duration.ofSeconds(30)).GET();
		if (bearer != null) {
			req.header("Authorization", "Bearer " + bearer);
		}
		if (internalToken != null) {
			req.header("X-Internal-Token", internalToken);
		}
		return http.send(req.build(), HttpResponse.BodyHandlers.ofString());
	}
}
