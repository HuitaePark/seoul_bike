package com.seoulbike.backend.plan;

import static org.assertj.core.api.Assertions.assertThat;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import java.time.Instant;
import java.time.LocalDate;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.ThreadLocalRandom;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.web.server.LocalServerPort;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.kafka.config.KafkaListenerEndpointRegistry;
import org.springframework.kafka.listener.MessageListenerContainer;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ArrayNode;
import tools.jackson.databind.node.ObjectNode;

import com.seoulbike.backend.IntegrationTest;
import com.seoulbike.backend.common.SeoulTime;

/**
 * POST /internal/plans/solve (business-mcp request_route_plan). 기준 정보(구역·대여소·트럭)는 시작할 때 올라간 실제 값을 쓰고,
 * 실시간 재고·제약·활성 계획은 행을 넣는다. 가짜 optimizer가 받은 SolveRequest로 입력 규칙을 보고, 답하지 않게 두어 임시안을 본다.
 * 근무조는 테스트마다 S02의 서로 다른 미래 날짜라 근무조 시작이 계산 기준 시각(asOf)이다.
 */
@IntegrationTest
class SolveApiTest {

	private static final String TOKEN = "test-internal-token";
	private static final String REGION = "S02";

	@LocalServerPort
	int port;

	@Autowired
	JsonMapper json;

	@Autowired
	JdbcClient jdbc;

	@Autowired
	FakeOptimizer optimizer;

	@Autowired
	KafkaListenerEndpointRegistry listeners;

	private final HttpClient http = HttpClient.newHttpClient();

	/** S02의 운영 중인 대여소 앞 6곳. */
	private List<String> st;

	/** S02의 운영 중인 트럭. */
	private List<String> trucks;

	@BeforeEach
	void setUp() throws InterruptedException {
		jdbc.sql("delete from live.station_status where station_id in (select station_id from ref.stations where region_id = :r)")
			.param("r", REGION)
			.update();
		st = jdbc.sql("select station_id from ref.stations where region_id = :r and active order by station_id limit 6")
			.param("r", REGION)
			.query(String.class)
			.list();
		trucks = jdbc.sql("select truck_id from ref.trucks where region_id = :r and active order by truck_id")
			.param("r", REGION)
			.query(String.class)
			.list();
		assertThat(trucks).hasSizeGreaterThanOrEqualTo(2);
		// 소비 그룹이 파티션을 받기 전에 보내면 첫 왕복이 기다림 한도를 넘길 수 있다.
		Instant deadline = Instant.now().plusSeconds(60);
		for (MessageListenerContainer c : listeners.getAllListenerContainers()) {
			while (c.getAssignedPartitions() == null || c.getAssignedPartitions().isEmpty()) {
				assertThat(Instant.now()).as(c.getListenerId() + " 파티션 배정").isBefore(deadline);
				Thread.sleep(100);
			}
		}
	}

	@Test
	void 계획_초안은_구역의_위험_대여소와_트럭을_차고지에서_근무조_끝까지로_보낸다() throws Exception {
		String day = futureDay();
		String shift = REGION + ":" + day + ":AM";
		observe(st.get(0), 0, 10); // EMPTY: 3대 배치, 2 × 3 = 6
		observe(st.get(1), 13, 10); // OVERFLOW: 3대 회수, 1 × 3 = 3
		observe(st.get(2), 5, 10); // 위험 없음: 보내지 않는다
		answerOk(shift, json.createArrayNode());

		var res = solve(body(shift, "PLAN"));

		assertThat(res.statusCode()).as(res.body()).isEqualTo(200);
		JsonNode sent = optimizer.lastRequest(shift);
		assertThat(sent.path("requestId").asString()).isEqualTo(shift + ":0:1");
		assertThat(sent.path("kind").asString()).isEqualTo("PLAN");
		assertThat(sent.path("asOf").asString()).isEqualTo(day + "T07:00:00+09:00");
		var depot = jdbc.sql("select depot_lat, depot_lon from ref.regions where region_id = :r").param("r", REGION)
			.query((rs, i) -> List.of(rs.getDouble(1), rs.getDouble(2))).single();
		assertThat(sent.path("depot").path("lat").asDouble()).isEqualTo(depot.get(0));
		assertThat(sent.path("depot").path("lon").asDouble()).isEqualTo(depot.get(1));

		JsonNode stations = sent.path("stations");
		assertThat(ids(stations, "stationId")).containsExactly(st.get(0), st.get(1));
		assertThat(stations.get(0).path("neededBikes").asInt()).isEqualTo(-3);
		assertThat(stations.get(0).path("priority").asDouble()).isEqualTo(6.0);
		assertThat(stations.get(0).path("kind").asString()).isEqualTo("EMPTY");
		assertThat(stations.get(1).path("neededBikes").asInt()).isEqualTo(3);
		assertThat(stations.get(1).path("kind").asString()).isEqualTo("OVERFLOW");
		var ref = position(st.get(0));
		assertThat(stations.get(0).path("lat").asDouble()).isEqualTo(ref.get(0));

		assertThat(ids(sent.path("trucks"), "truckId")).containsExactlyElementsOf(trucks);
		for (JsonNode t : sent.path("trucks")) {
			assertThat(t.path("capacity").asInt()).isEqualTo(15);
			assertThat(t.path("load").asInt()).isZero();
			assertThat(t.path("lat").asDouble()).isEqualTo(depot.get(0));
			assertThat(t.path("lon").asDouble()).isEqualTo(depot.get(1));
			assertThat(t.path("availableUntil").asString()).isEqualTo(day + "T10:00:00+09:00");
			assertThat(t.has("fixedStationIds")).isFalse();
		}
		assertThat(sent.path("blockedStationIds")).isEmpty();
		JsonNode params = sent.path("params");
		assertThat(params.path("solutionLimit").asInt()).isEqualTo(50);
		assertThat(params.path("serviceMinutesBase").asDouble()).isEqualTo(3.0);
		assertThat(params.path("serviceMinutesPerBike").asDouble()).isEqualTo(0.5);

		JsonNode candidate = json.readTree(res.body());
		assertThat(candidate.path("status").asString()).isEqualTo("OK");
		assertThat(candidate.path("plan").path("status").asString()).isEqualTo("PROPOSED");
		assertThat(candidate.path("plan").path("provisional").asBoolean()).isFalse();
		assertThat(candidate.path("grade").isNull()).isTrue();
	}

	@Test
	void 근무조_제약과_제외_대여소를_반영한다() throws Exception {
		String day = futureDay();
		String shift = REGION + ":" + day + ":AM";
		Instant start = OffsetDateTime.parse(day + "T07:00:00+09:00").toInstant();
		String blocked = st.get(0), override = st.get(1), adjusted = st.get(2), boosted = st.get(3), excluded = st.get(4),
				untouched = st.get(5);
		observe(blocked, 0, 10);
		observe(override, 13, 10);
		observe(adjusted, 5, 10);
		observe(boosted, 0, 10);
		observe(excluded, 0, 10);
		observe(untouched, 0, 10);
		UUID task = task(shift, "REQUEST");
		constraint(task, shift, "STATION_BLOCKED", List.of(blocked), null, start.plusSeconds(3600), start.plusSeconds(7200));
		constraint(task, shift, "INVENTORY_OVERRIDE", List.of(override), 5, start, start.plusSeconds(3 * 3600));
		constraint(task, shift, "TARGET_ADJUST", List.of(adjusted), 4, start, start.plusSeconds(3 * 3600));
		constraint(task, shift, "PRIORITY_BOOST", List.of(boosted), null, start, start.plusSeconds(3 * 3600));
		// 근무조 전에 끝난 제약과 다른 근무조의 제약은 쓰지 않는다.
		constraint(task, shift, "STATION_BLOCKED", List.of(untouched), null, start.minusSeconds(7200), start);
		constraint(task, REGION + ":" + day + ":PM", "STATION_SUSPENDED", List.of(untouched), null, start,
				start.plusSeconds(3 * 3600));
		answerOk(shift, json.createArrayNode());

		ObjectNode body = body(shift, "PLAN");
		body.putArray("excludeStationIds").add(excluded);
		body.putObject("requestedBy").put("taskId", task.toString());
		var res = solve(body);

		assertThat(res.statusCode()).as(res.body()).isEqualTo(200);
		JsonNode sent = optimizer.lastRequest(shift);
		assertThat(sent.path("traceId").isMissingNode()).isTrue();
		// 막을 대여소도 stations에 남는다 (optimizer는 stations에 없는 ID를 막지 못한다). 결과에서 BLOCKED 미처리가 된다.
		assertThat(ids(sent.path("blockedStationIds"), null)).containsExactlyElementsOf(List.of(blocked, excluded)
			.stream().sorted().toList());
		Map<String, JsonNode> stations = byId(sent.path("stations"));
		assertThat(stations).containsOnlyKeys(blocked, adjusted, boosted, excluded, untouched);
		// 재고 13 → 현장 5대: 위험이 없어 빠진다.
		assertThat(stations).doesNotContainKey(override);
		// 목표 +4대: 4대 배치, 우선순위 4 × 2.
		assertThat(stations.get(adjusted).path("neededBikes").asInt()).isEqualTo(-4);
		assertThat(stations.get(adjusted).path("priority").asDouble()).isEqualTo(8.0);
		assertThat(stations.get(adjusted).path("kind").asString()).isEqualTo("EMPTY");
		assertThat(stations.get(boosted).path("priority").asDouble()).isEqualTo(12.0);
		assertThat(stations.get(untouched).path("priority").asDouble()).isEqualTo(6.0);
	}

	@Test
	void 답이_없으면_탐욕_임시안을_후보로_남기고_같은_승인으로_활성화된다() throws Exception {
		String day = futureDay();
		String shift = REGION + ":" + day + ":AM";
		String pickup = st.get(0), dropoff = st.get(1), excluded = st.get(2);
		observe(pickup, 13, 10);
		observe(dropoff, 0, 10);
		observe(excluded, 13, 10);
		ObjectNode body = body(shift, "PLAN");
		body.putArray("excludeStationIds").add(excluded);
		Instant started = Instant.now();

		var res = solve(body);

		assertThat(Duration.between(started, Instant.now())).isGreaterThanOrEqualTo(Duration.ofSeconds(5));
		assertThat(res.statusCode()).as(res.body()).isEqualTo(200);
		JsonNode candidate = json.readTree(res.body());
		assertThat(candidate.path("status").asString()).isEqualTo("TIMEOUT_PROVISIONAL");
		JsonNode plan = candidate.path("plan");
		assertThat(plan.path("provisional").asBoolean()).isTrue();
		assertThat(plan.path("status").asString()).isEqualTo("PROPOSED");
		assertThat(plan.path("explanation").asString()).contains("임시안");
		assertThat(ids(plan.path("routes"), "truckId")).containsExactlyElementsOf(trucks);
		// 첫 트럭이 회수(3대)한 뒤 그 자전거로 배치한다. 둘째 트럭은 할 일이 남지 않는다.
		JsonNode stops = plan.path("routes").get(0).path("stops");
		assertThat(ids(stops, "stationId")).containsExactly(pickup, dropoff);
		assertThat(ids(stops, "action")).containsExactly("PICKUP", "DROPOFF");
		assertThat(stops.get(0).path("bikes").asInt()).isEqualTo(3);
		assertThat(stops.get(0).path("loadAfter").asInt()).isEqualTo(3);
		assertThat(stops.get(1).path("loadAfter").asInt()).isZero();
		assertThat(stops.get(0).path("status").asString()).isEqualTo("PENDING");
		Instant eta = OffsetDateTime.parse(stops.get(0).path("eta").asString()).toInstant();
		assertThat(stops.get(0).path("eta").asString()).endsWith("+09:00");
		assertThat(eta).isAfter(OffsetDateTime.parse(day + "T07:00:00+09:00").toInstant());
		assertThat(plan.path("routes").get(1).path("stops")).isEmpty();
		assertThat(plan.path("unserved")).hasSize(1);
		assertThat(plan.path("unserved").get(0).path("stationId").asString()).isEqualTo(excluded);
		assertThat(plan.path("unserved").get(0).path("reason").asString()).isEqualTo("BLOCKED");

		String planId = candidate.path("candidateId").asString();
		assertThat(jdbc.sql("select provisional::text || ' ' || request_id from plans where plan_id = :p").param("p", planId)
			.query(String.class).single()).isEqualTo("true " + shift + ":0:1");
		// 화면·에이전트가 보는 계획에도 임시안 표시가 남는다.
		UUID task = task(shift, "PLAN_DRAFT");
		UUID card = approval(task, "SUBMIT_PLAN", planId);
		var activated = call("POST", "/internal/plans/" + planId + "/activate",
				json.writeValueAsString(json.createObjectNode().put("approvalId", card.toString()).put("taskId", task.toString())));
		assertThat(activated.statusCode()).as(activated.body()).isEqualTo(200);
		JsonNode current = json.readTree(call("GET", "/internal/shifts/" + shift + "/plan", null).body());
		assertThat(current.path("status").asString()).isEqualTo("ACTIVE");
		assertThat(current.path("provisional").asBoolean()).isTrue();
	}

	@Test
	void 재경로는_활성_계획의_트럭을_마지막으로_끝낸_정류장에서_그_적재로_다시_푼다() throws Exception {
		String day = futureDay();
		String shift = REGION + ":" + day + ":AM";
		String done = st.get(0), pending = st.get(1), other = st.get(2), fresh = st.get(3);
		String t1 = trucks.get(0), t2 = trucks.get(1);
		ArrayNode routes = json.createArrayNode();
		routes.add(route(t1, 2, stop(1, done, "PICKUP", 3, 5, "DONE"), stop(2, pending, "DROPOFF", 3, 2, "PENDING")));
		routes.add(route(t2, 0, stop(1, other, "PICKUP", 3, 3, "PENDING")));
		insertActive(shift, routes);
		observe(done, 13, 10); // 위험이지만 이미 끝낸 정류장이라 다시 넣지 않는다
		observe(pending, 0, 10);
		observe(other, 13, 10); // 다시 풀지 않는 트럭의 남은 정류장이라 넣지 않는다
		observe(fresh, 0, 10);
		answerOk(shift, json.createArrayNode().add(route(t1, 5, stop(1, pending, "DROPOFF", 3, 2, "PENDING"),
				stop(2, fresh, "DROPOFF", 2, 0, "PENDING"))));

		ObjectNode body = body(shift, "REPLAN");
		body.putArray("truckIds").add(t1);
		var res = solve(body);

		assertThat(res.statusCode()).as(res.body()).isEqualTo(200);
		JsonNode sent = optimizer.lastRequest(shift);
		assertThat(sent.path("requestId").asString()).isEqualTo(shift + ":1:1");
		assertThat(sent.path("kind").asString()).isEqualTo("REPLAN");
		assertThat(ids(sent.path("stations"), "stationId")).containsExactlyInAnyOrder(pending, fresh);
		assertThat(sent.path("trucks")).hasSize(1);
		JsonNode truck = sent.path("trucks").get(0);
		assertThat(truck.path("truckId").asString()).isEqualTo(t1);
		assertThat(truck.path("load").asInt()).isEqualTo(5);
		var at = position(done);
		assertThat(truck.path("lat").asDouble()).isEqualTo(at.get(0));
		assertThat(truck.path("lon").asDouble()).isEqualTo(at.get(1));
		assertThat(truck.path("availableUntil").asString()).isEqualTo(day + "T10:00:00+09:00");
		assertThat(ids(truck.path("fixedStationIds"), null)).containsExactly(pending);

		// 후보는 활성 계획 전체다: 끝낸 정류장 뒤에 새 정류장을 잇고, 다시 풀지 않은 트럭은 그대로 둔다.
		JsonNode plan = json.readTree(res.body()).path("plan");
		assertThat(ids(plan.path("routes"), "truckId")).containsExactly(t1, t2);
		JsonNode merged = plan.path("routes").get(0);
		assertThat(merged.path("startLoad").asInt()).isEqualTo(2);
		assertThat(ids(merged.path("stops"), "stationId")).containsExactly(done, pending, fresh);
		assertThat(ids(merged.path("stops"), "status")).containsExactly("DONE", "PENDING", "PENDING");
		assertThat(merged.path("stops").valueStream().map(s -> s.path("seq").asInt())).containsExactly(1, 2, 3);
		assertThat(plan.path("routes").get(1)).isEqualTo(routes.get(1));
		assertThat(json.readTree(res.body()).path("diffs")).hasSize(2);

		var unknownTruck = solve(body(shift, "REPLAN").set("truckIds", json.createArrayNode().add("TR-99")));
		assertThat(unknownTruck.statusCode()).isEqualTo(404);
		assertThat(code(unknownTruck)).isEqualTo("TRUCK_NOT_FOUND");
		assertThat(ids(json.readTree(unknownTruck.body()).path("suggestions"), null)).containsExactly(t1, t2);
	}

	@Test
	void 근무조_형식_구역_활성_계획_대여소_작업을_검사한다() throws Exception {
		var malformed = solve(body("S02-2026-10-21-AM", "PLAN"));
		assertThat(malformed.statusCode()).isEqualTo(400);
		assertThat(code(malformed)).isEqualTo("INVALID_REQUEST");
		assertThat(solve(body(REGION + ":2026-02-30:AM", "PLAN")).statusCode()).isEqualTo(400);
		assertThat(code(solve(json.createObjectNode().put("shiftId", REGION + ":" + futureDay() + ":AM"))))
			.isEqualTo("INVALID_REQUEST");

		var unknownRegion = solve(body("N09:" + futureDay() + ":AM", "PLAN"));
		assertThat(unknownRegion.statusCode()).isEqualTo(404);
		assertThat(code(unknownRegion)).isEqualTo("SHIFT_NOT_FOUND");

		var noActive = solve(body(REGION + ":" + futureDay() + ":PM", "REPLAN"));
		assertThat(noActive.statusCode()).isEqualTo(404);
		assertThat(code(noActive)).isEqualTo("SHIFT_NOT_FOUND");
		assertThat(detail(noActive)).contains("활성 계획이 없다");

		ObjectNode unknownStation = body(REGION + ":" + futureDay() + ":EVE", "PLAN");
		unknownStation.putArray("excludeStationIds").add("ST-99999999");
		var station = solve(unknownStation);
		assertThat(station.statusCode()).isEqualTo(404);
		assertThat(code(station)).isEqualTo("STATION_NOT_FOUND");

		ObjectNode unknownTask = body(REGION + ":" + futureDay() + ":AM", "PLAN");
		unknownTask.putObject("requestedBy").put("taskId", UUID.randomUUID().toString());
		assertThat(code(solve(unknownTask))).isEqualTo("INVALID_REQUEST");

		var ended = solve(body(REGION + ":2020-01-01:AM", "PLAN"));
		assertThat(ended.statusCode()).isEqualTo(409);
		assertThat(code(ended)).isEqualTo("SHIFT_ENDED");

		var noToken = http.send(HttpRequest.newBuilder(URI.create("http://localhost:" + port + "/internal/plans/solve"))
			.header("Content-Type", "application/json")
			.POST(HttpRequest.BodyPublishers.ofString(json.writeValueAsString(body(REGION + ":2026-12-01:AM", "PLAN"))))
			.build(), HttpResponse.BodyHandlers.ofString());
		assertThat(noToken.statusCode()).isEqualTo(401);
	}

	// ── 행 넣기 ────────────────────────────

	/** 2027년 뒤의 아무 날. 테스트마다 근무조가 겹치지 않는다. */
	private static String futureDay() {
		return LocalDate.of(2027, 1, 1).plusDays(ThreadLocalRandom.current().nextInt(100_000)).toString();
	}

	private void observe(String stationId, int bikes, int docks) {
		jdbc.sql("insert into live.station_status (observed_at, station_id, bikes, docks) values (:at, :id, :bikes, :docks)")
			.param("at", OffsetDateTime.now(ZoneOffset.UTC).minusMinutes(5))
			.param("id", stationId)
			.param("bikes", bikes)
			.param("docks", docks)
			.update();
	}

	private List<Double> position(String stationId) {
		return jdbc.sql("select lat, lon from ref.stations where station_id = :id").param("id", stationId)
			.query((rs, i) -> List.of(rs.getDouble(1), rs.getDouble(2))).single();
	}

	private UUID task(String shiftId, String type) {
		UUID taskId = UUID.randomUUID();
		jdbc.sql("""
				insert into ai_tasks (task_id, type, status, shift_id, created_by)
				values (:taskId, :type, 'RUNNING', :shiftId, 'u-manager')
				""")
			.param("taskId", taskId)
			.param("type", type)
			.param("shiftId", shiftId)
			.update();
		return taskId;
	}

	private UUID approval(UUID taskId, String kind, String planId) {
		UUID approvalId = UUID.randomUUID();
		ObjectNode card = json.createObjectNode().put("approvalId", approvalId.toString()).put("kind", kind)
			.put("approver", "MANAGER").put("summary", "테스트 카드").put("planId", planId);
		jdbc.sql("""
				insert into approvals (approval_id, task_id, kind, approver_role, status, request, decided_by, decided_at)
				values (:approvalId, :taskId, :kind, 'MANAGER', 'APPROVED', cast(:request as jsonb), 'u-manager', clock_timestamp())
				""")
			.param("approvalId", approvalId)
			.param("taskId", taskId)
			.param("kind", kind)
			.param("request", json.writeValueAsString(card))
			.update();
		return approvalId;
	}

	/** 관리자가 승인한 제약 (POST /internal/constraints가 남기는 행과 같다). */
	private void constraint(UUID taskId, String shiftId, String type, List<String> stationIds, Integer value,
			Instant from, Instant to) {
		jdbc.sql("""
				insert into constraints (constraint_id, approval_id, task_id, shift_id, type, station_ids, value,
				                         valid_from, valid_to, reason)
				values (:id, :approvalId, :taskId, :shiftId, :type, cast(:stationIds as jsonb), :value, :from, :to, '테스트')
				""")
			.param("id", UUID.randomUUID())
			.param("approvalId", approval(taskId, "ADD_CONSTRAINT", null))
			.param("taskId", taskId)
			.param("shiftId", shiftId)
			.param("type", type)
			.param("stationIds", json.writeValueAsString(stationIds))
			.param("value", value)
			.param("from", OffsetDateTime.ofInstant(from, ZoneOffset.UTC))
			.param("to", OffsetDateTime.ofInstant(to, ZoneOffset.UTC))
			.update();
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
			.put("bikes", bikes).put("loadAfter", loadAfter).put("status", status);
	}

	/** 가짜 optimizer가 이 근무조 요청에 OK로 이 경로를 돌려준다 (진짜 worker처럼 상관관계를 그대로 돌려준다). */
	private void answerOk(String shiftId, ArrayNode routes) {
		optimizer.answers.put(shiftId, request -> {
			ObjectNode r = json.createObjectNode();
			r.put("eventId", UUID.randomUUID().toString());
			r.put("schemaVersion", 1);
			r.put("occurredAt", SeoulTime.now());
			r.put("shiftId", shiftId);
			r.put("requestId", request.path("requestId").asString());
			r.put("status", "OK");
			r.set("routes", routes);
			r.putArray("unserved");
			return List.of(json.writeValueAsString(r));
		});
	}

	// ── HTTP ────────────────────────────

	private ObjectNode body(String shiftId, String kind) {
		return json.createObjectNode().put("shiftId", shiftId).put("kind", kind);
	}

	private HttpResponse<String> solve(ObjectNode body) throws Exception {
		return call("POST", "/internal/plans/solve", json.writeValueAsString(body));
	}

	private HttpResponse<String> call(String method, String path, String body) throws Exception {
		var req = HttpRequest.newBuilder(URI.create("http://localhost:" + port + path)).timeout(Duration.ofSeconds(30))
			.header("X-Internal-Token", TOKEN)
			.method(method, body == null ? HttpRequest.BodyPublishers.noBody() : HttpRequest.BodyPublishers.ofString(body));
		if (body != null) {
			req.header("Content-Type", "application/json");
		}
		return http.send(req.build(), HttpResponse.BodyHandlers.ofString());
	}

	private String code(HttpResponse<String> res) {
		return json.readTree(res.body()).path("code").asString();
	}

	private String detail(HttpResponse<String> res) {
		return json.readTree(res.body()).path("detail").asString();
	}

	/** 배열 원소의 field 값(field가 null이면 원소 자체의 문자열). */
	private static List<String> ids(JsonNode array, String field) {
		List<String> out = new ArrayList<>();
		array.forEach(n -> out.add(field == null ? n.asString() : n.path(field).asString()));
		return out;
	}

	private static Map<String, JsonNode> byId(JsonNode stations) {
		Map<String, JsonNode> out = new java.util.LinkedHashMap<>();
		stations.forEach(s -> out.put(s.path("stationId").asString(), s));
		return out;
	}
}
