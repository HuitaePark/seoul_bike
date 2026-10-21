package com.seoulbike.backend.plan;

import static org.assertj.core.api.Assertions.assertThat;

import java.time.Duration;
import java.time.Instant;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.BlockingQueue;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.LinkedBlockingQueue;
import java.util.concurrent.TimeUnit;
import java.util.function.Function;

import org.apache.kafka.clients.consumer.ConsumerRecord;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.context.TestConfiguration;
import org.springframework.context.annotation.Import;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.kafka.annotation.KafkaListener;
import org.springframework.kafka.config.KafkaListenerEndpointRegistry;
import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.kafka.listener.MessageListenerContainer;
import org.springframework.kafka.support.KafkaHeaders;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ArrayNode;
import tools.jackson.databind.node.ObjectNode;

import com.seoulbike.backend.DeadLetters;
import com.seoulbike.backend.TestInfra;

/**
 * solve.requests → (가짜 optimizer) → solve.results 왕복과 계획 후보 저장. 가짜 optimizer는 근무조마다 정한 답을 보낸다
 * (진짜 worker처럼 requestId·shiftId·traceId를 그대로 돌려준다). 테스트마다 새 근무조를 써서 서로 섞이지 않는다.
 * 기다림 한도는 TestInfra의 app.solve.timeout(5초)이다.
 */
@SpringBootTest(properties = TestInfra.PROPS)
@Import({ TestInfra.class, SolveRoundTripTest.FakeOptimizer.class })
class SolveRoundTripTest {

	private static final String DLQ = SolveTopics.SOLVE_RESULTS + ".dlq";
	private static final Instant T0 = Instant.parse("2026-10-20T23:00:00Z");

	/** 테스트 전용 optimizer. 근무조마다 요청 → 보낼 결과들을 정한다. 정하지 않은 근무조에는 답하지 않는다. */
	@TestConfiguration(proxyBeanMethods = false)
	static class FakeOptimizer {

		final Map<String, Function<JsonNode, List<String>>> answers = new ConcurrentHashMap<>();
		final Map<String, BlockingQueue<JsonNode>> received = new ConcurrentHashMap<>();
		private final KafkaTemplate<String, String> kafka;
		private final JsonMapper json;

		FakeOptimizer(KafkaTemplate<String, String> kafka, JsonMapper json) {
			this.kafka = kafka;
			this.json = json;
		}

		@KafkaListener(topics = SolveTopics.SOLVE_REQUESTS, groupId = "test-fake-optimizer")
		void onRequest(String value) {
			JsonNode request = json.readTree(value);
			String shiftId = request.path("shiftId").asString();
			received.computeIfAbsent(shiftId, s -> new LinkedBlockingQueue<>()).add(request);
			for (String result : answers.getOrDefault(shiftId, r -> List.of()).apply(request)) {
				kafka.send(SolveTopics.SOLVE_RESULTS, shiftId, result);
			}
		}

		JsonNode lastRequest(String shiftId) throws InterruptedException {
			JsonNode request = received.computeIfAbsent(shiftId, s -> new LinkedBlockingQueue<>()).poll(10, TimeUnit.SECONDS);
			assertThat(request).as("가짜 optimizer가 받은 요청").isNotNull();
			return request;
		}
	}

	@Autowired
	SolveGateway gateway;

	@Autowired
	FakeOptimizer optimizer;

	@Autowired
	KafkaTemplate<String, String> kafka;

	@Autowired
	KafkaListenerEndpointRegistry listeners;

	@Autowired
	JdbcClient jdbc;

	@Autowired
	JsonMapper json;

	/** 소비 그룹이 파티션을 받기 전에 보내면 첫 왕복이 기다림 한도를 넘길 수 있다. */
	@BeforeEach
	void listenersAssigned() throws InterruptedException {
		Instant deadline = Instant.now().plusSeconds(60);
		for (MessageListenerContainer c : listeners.getAllListenerContainers()) {
			while (c.getAssignedPartitions() == null || c.getAssignedPartitions().isEmpty()) {
				assertThat(Instant.now()).as(c.getListenerId() + " 파티션 배정").isBefore(deadline);
				Thread.sleep(100);
			}
		}
	}

	@Test
	void okResultBecomesProposedCandidateAndSeqGrows() throws Exception {
		String shift = shift();
		ArrayNode routes = routes(route("truck-1", stop(1, "ST-1", "PICKUP", 5, 10), stop(2, "ST-2", "DROPOFF", 5, 20)));
		optimizer.answers.put(shift, r -> List.of(result(r, "OK", routes, null)));

		SolveOutcome first = gateway.solve(shift, SolveGateway.Kind.PLAN, inputs(), null, "trace-a");

		assertThat(first.status()).isEqualTo(SolveOutcome.Status.OK);
		assertThat(first.requestId()).isEqualTo(shift + ":0:1");
		JsonNode sent = optimizer.lastRequest(shift);
		assertThat(sent.path("requestId").asString()).isEqualTo(first.requestId());
		assertThat(sent.path("kind").asString()).isEqualTo("PLAN");
		assertThat(sent.path("schemaVersion").asInt()).isEqualTo(1);
		assertThat(sent.path("traceId").asString()).isEqualTo("trace-a");
		assertThat(sent.path("params").path("solutionLimit").asInt()).isEqualTo(100);

		ObjectNode candidate = first.candidate();
		String planId = candidate.path("candidateId").asString();
		assertThat(planId).startsWith("PL-");
		assertThat(candidate.path("status").asString()).isEqualTo("OK");
		JsonNode plan = candidate.path("plan");
		assertThat(plan.path("planId").asString()).isEqualTo(planId);
		assertThat(plan.path("version").asInt()).isEqualTo(1);
		assertThat(plan.path("status").asString()).isEqualTo("PROPOSED");
		assertThat(plan.path("provisional").asBoolean()).isFalse();
		assertThat(plan.path("routes")).isEqualTo(routes);
		assertThat(candidate.path("grade").isNull()).isTrue();
		assertThat(candidate.path("requiredApprovers").isEmpty()).isTrue();
		assertThat(candidate.path("diffs").isEmpty()).isTrue();

		assertThat(jdbc.sql("select status || ' ' || version || ' ' || request_id from plans where plan_id = :id")
			.param("id", planId).query(String.class).single()).isEqualTo("PROPOSED 1 " + first.requestId());
		assertThat(requestStatus(first.requestId())).isEqualTo("OK");
		assertThat(json.readTree(jdbc.sql("select request::text from solve_requests where request_id = :id")
			.param("id", first.requestId()).query(String.class).single())).isEqualTo(sent);

		SolveOutcome second = gateway.solve(shift, SolveGateway.Kind.PLAN, inputs(), null, "trace-b");
		assertThat(second.requestId()).isEqualTo(shift + ":0:2");
		assertThat(second.candidate().path("plan").path("version").asInt()).isEqualTo(2);
	}

	/** 활성 계획이 있으면 트럭마다 비교한다. 트럭 하나에서 정류장 하나가 빠지면 현장 보고는 MINOR, 관리자 요청은 MAJOR. */
	@Test
	void candidateAgainstActivePlanIsGraded() throws Exception {
		String shift = shift();
		ArrayNode active = routes(
				route("truck-1", stop(1, "ST-1", "PICKUP", 5, 10), stop(2, "ST-2", "PICKUP", 3, 20), stop(3, "ST-3", "DROPOFF", 8, 30)),
				route("truck-2", stop(1, "ST-9", "PICKUP", 2, 15)));
		jdbc.sql("""
				insert into plans (plan_id, shift_id, version, status, routes, unserved)
				values (:id, :shift, 1, 'ACTIVE', cast(:routes as jsonb), '[]')
				""")
			.param("id", "PL-" + UUID.randomUUID())
			.param("shift", shift)
			.param("routes", json.writeValueAsString(active))
			.update();
		ArrayNode replanned = routes(route("truck-1", stop(1, "ST-1", "PICKUP", 5, 10), stop(2, "ST-3", "DROPOFF", 5, 24)),
				route("truck-2", stop(1, "ST-9", "PICKUP", 2, 15)));
		optimizer.answers.put(shift, r -> List.of(result(r, "OK", replanned, null)));
		UUID fieldReportTask = UUID.randomUUID();
		jdbc.sql("""
				insert into ai_tasks (task_id, type, status, shift_id, created_by)
				values (:id, 'FIELD_REPORT', 'RUNNING', :shift, 'u-driver-1')
				""")
			.param("id", fieldReportTask)
			.param("shift", shift)
			.update();

		SolveOutcome fromReport = gateway.solve(shift, SolveGateway.Kind.REPLAN, inputs(), fieldReportTask, null);

		assertThat(fromReport.requestId()).isEqualTo(shift + ":1:1");
		ObjectNode candidate = fromReport.candidate();
		assertThat(candidate.path("plan").path("version").asInt()).isEqualTo(2);
		assertThat(candidate.path("grade").asString()).isEqualTo("MINOR");
		assertThat(candidate.path("requiredApprovers")).isEqualTo(json.createArrayNode().add("DRIVER"));
		JsonNode diffs = candidate.path("diffs");
		assertThat(diffs.valueStream().map(d -> d.path("truckId").asString())).containsExactly("truck-1", "truck-2");
		JsonNode truck1 = diffs.get(0);
		assertThat(truck1.path("before")).isEqualTo(active.get(0).path("stops"));
		assertThat(truck1.path("after")).isEqualTo(replanned.get(0).path("stops"));
		assertThat(truck1.path("addedMinutes").asDouble()).isEqualTo(-6.0);
		assertThat(diffs.get(1).path("before")).isEqualTo(diffs.get(1).path("after"));

		SolveOutcome fromManager = gateway.solve(shift, SolveGateway.Kind.REPLAN, inputs(), null, null);
		assertThat(fromManager.requestId()).isEqualTo(shift + ":1:2");
		assertThat(fromManager.candidate().path("grade").asString()).isEqualTo("MAJOR");
		assertThat(fromManager.candidate().path("requiredApprovers"))
			.isEqualTo(json.createArrayNode().add("MANAGER").add("DRIVER"));
	}

	@Test
	void duplicateResultMakesOneCandidate() throws Exception {
		String shift = shift();
		ArrayNode routes = routes(route("truck-1", stop(1, "ST-1", "PICKUP", 5, 10)));
		optimizer.answers.put(shift, r -> {
			String once = result(r, "OK", routes, null);
			return List.of(once, once);
		});

		SolveOutcome first = gateway.solve(shift, SolveGateway.Kind.PLAN, inputs(), null, null);
		// 같은 키라 같은 파티션이다. 두 번째 요청의 결과를 받았으면 그 앞의 중복 결과도 처리가 끝났다.
		SolveOutcome second = gateway.solve(shift, SolveGateway.Kind.PLAN, inputs(), null, null);

		assertThat(first.status()).isEqualTo(SolveOutcome.Status.OK);
		assertThat(second.status()).isEqualTo(SolveOutcome.Status.OK);
		assertThat(jdbc.sql("select count(*) from plans where request_id = :id").param("id", first.requestId())
			.query(Integer.class).single()).isEqualTo(1);
		assertThat(jdbc.sql("select count(*) from plans where shift_id = :shift").param("shift", shift)
			.query(Integer.class).single()).isEqualTo(2);
	}

	@Test
	void optimizerFailureReturnsErrorWithoutPlan() throws Exception {
		String shift = shift();
		optimizer.answers.put(shift, r -> List.of(result(r, "FAILED", null, "NO_SOLUTION: 트럭 용량으로 풀 수 없다")));

		SolveOutcome failed = gateway.solve(shift, SolveGateway.Kind.PLAN, inputs(), null, null);

		assertThat(failed.status()).isEqualTo(SolveOutcome.Status.FAILED);
		assertThat(failed.error()).startsWith("NO_SOLUTION");
		assertThat(failed.candidate()).isNull();
		assertThat(requestStatus(failed.requestId())).isEqualTo("FAILED");

		optimizer.answers.put(shift, r -> List.of(result(r, "TIMEOUT", null, "SOLVER_TIMEOUT: 해를 찾지 못했다")));
		SolveOutcome timeout = gateway.solve(shift, SolveGateway.Kind.PLAN, inputs(), null, null);
		assertThat(timeout.status()).isEqualTo(SolveOutcome.Status.TIMEOUT);
		assertThat(timeout.error()).startsWith("SOLVER_TIMEOUT");
		assertThat(jdbc.sql("select count(*) from plans where shift_id = :shift").param("shift", shift)
			.query(Integer.class).single()).isZero();
	}

	/** 답이 없으면 한도에서 TIMED_OUT. 늦게 온 결과는 요청 행에 저장되지만 후보는 생기지 않는다. */
	@Test
	void noAnswerTimesOutAndLateResultIsStillStored() throws Exception {
		String shift = shift();
		Instant start = Instant.now();

		SolveOutcome outcome = gateway.solve(shift, SolveGateway.Kind.PLAN, inputs(), null, null);

		Duration waited = Duration.between(start, Instant.now());
		assertThat(outcome.status()).isEqualTo(SolveOutcome.Status.TIMED_OUT);
		assertThat(waited).isBetween(Duration.ofSeconds(5), Duration.ofSeconds(8));
		assertThat(requestStatus(outcome.requestId())).isEqualTo("PENDING");

		JsonNode request = optimizer.lastRequest(shift);
		kafka.send(SolveTopics.SOLVE_RESULTS, shift,
				result(request, "OK", routes(route("truck-1", stop(1, "ST-1", "PICKUP", 5, 10))), null))
			.get(10, TimeUnit.SECONDS);
		Instant deadline = Instant.now().plusSeconds(15);
		while (!"OK".equals(requestStatus(outcome.requestId()))) {
			assertThat(Instant.now()).as("늦은 결과 저장").isBefore(deadline);
			Thread.sleep(100);
		}
		assertThat(jdbc.sql("select count(*) from plans where shift_id = :shift").param("shift", shift)
			.query(Integer.class).single()).isZero();
	}

	@Test
	void resultOfUnknownRequestIsRetriedThenDeadLettered() throws Exception {
		String shift = shift();
		ObjectNode request = json.createObjectNode().put("shiftId", shift).put("requestId", shift + ":0:99");
		String result = result(request, "OK", routes(), null);
		String eventId = json.readTree(result).path("eventId").asString();
		kafka.send(SolveTopics.SOLVE_RESULTS, shift, result).get(10, TimeUnit.SECONDS);

		ConsumerRecord<String, String> dead = DeadLetters.await(kafka, DLQ, eventId);
		assertThat(DeadLetters.tries(dead)).isEqualTo(4);
		assertThat(DeadLetters.header(dead, KafkaHeaders.EXCEPTION_MESSAGE)).contains("모르는 경로 계산 요청");
	}

	@Test
	void malformedResultGoesStraightToDlq() throws Exception {
		String notJson = "not-json-" + UUID.randomUUID();
		kafka.send(SolveTopics.SOLVE_RESULTS, "s-dlq", notJson).get(10, TimeUnit.SECONDS);
		ConsumerRecord<String, String> dead = DeadLetters.await(kafka, DLQ, notJson);
		assertThat(DeadLetters.header(dead, KafkaHeaders.ORIGINAL_TOPIC)).isEqualTo(SolveTopics.SOLVE_RESULTS);
		assertThat(DeadLetters.tries(dead)).isEqualTo(1);

		String unknownStatus = UUID.randomUUID().toString();
		kafka.send(SolveTopics.SOLVE_RESULTS, "s-dlq", json.writeValueAsString(json.createObjectNode()
			.put("eventId", unknownStatus)
			.put("schemaVersion", 1)
			.put("shiftId", "s-dlq")
			.put("requestId", "s-dlq:0:1")
			.put("status", "DONE"))).get(10, TimeUnit.SECONDS);
		assertThat(DeadLetters.tries(DeadLetters.await(kafka, DLQ, unknownStatus))).isEqualTo(1);
	}

	private static String shift() {
		return "T-" + UUID.randomUUID();
	}

	private String requestStatus(String requestId) {
		return jdbc.sql("select status from solve_requests where request_id = :id").param("id", requestId)
			.query(String.class).single();
	}

	private ObjectNode inputs() {
		ObjectNode inputs = json.createObjectNode();
		inputs.putArray("stations");
		inputs.putArray("trucks");
		inputs.putObject("params").put("solutionLimit", 100);
		return inputs;
	}

	/** 진짜 worker의 결과 모양 (docs/specs/examples/optimizer/result.json). */
	private String result(JsonNode request, String status, ArrayNode routes, String error) {
		ObjectNode r = json.createObjectNode();
		r.put("eventId", UUID.randomUUID().toString());
		r.put("schemaVersion", 1);
		r.put("occurredAt", Instant.now().toString());
		r.put("shiftId", request.path("shiftId").asString());
		if (request.has("traceId")) {
			r.put("traceId", request.path("traceId").asString());
		}
		r.put("requestId", request.path("requestId").asString());
		r.put("status", status);
		if (routes != null) {
			r.set("routes", routes);
			r.putArray("unserved").addObject().put("stationId", "ST-77").put("priority", 0.4).put("reason", "시간 부족");
			r.putObject("stats").put("solveMs", 13).put("bikesMoved", 10);
		}
		if (error != null) {
			r.put("error", error);
		}
		return json.writeValueAsString(r);
	}

	private ArrayNode routes(ObjectNode... routes) {
		ArrayNode out = json.createArrayNode();
		for (ObjectNode route : routes) {
			out.add(route);
		}
		return out;
	}

	private ObjectNode route(String truckId, ObjectNode... stops) {
		ObjectNode route = json.createObjectNode().put("truckId", truckId).put("capacity", 15).put("startLoad", 0);
		ArrayNode out = route.putArray("stops");
		for (ObjectNode stop : stops) {
			out.add(stop);
		}
		return route;
	}

	private ObjectNode stop(int seq, String stationId, String action, int bikes, int etaMinutes) {
		return json.createObjectNode()
			.put("seq", seq)
			.put("stationId", stationId)
			.put("action", action)
			.put("bikes", bikes)
			.put("loadAfter", 0)
			.put("eta", T0.plusSeconds(etaMinutes * 60L).toString())
			.put("status", "PENDING");
	}
}
