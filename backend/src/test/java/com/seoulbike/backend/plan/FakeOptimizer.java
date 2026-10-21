package com.seoulbike.backend.plan;

import static org.assertj.core.api.Assertions.assertThat;

import java.util.List;
import java.util.Map;
import java.util.concurrent.BlockingQueue;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.LinkedBlockingQueue;
import java.util.concurrent.TimeUnit;
import java.util.function.Function;

import org.springframework.boot.test.context.TestConfiguration;
import org.springframework.kafka.annotation.KafkaListener;
import org.springframework.kafka.core.KafkaTemplate;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

/**
 * 테스트 전용 optimizer. 근무조마다 요청 → 보낼 결과들을 정한다. 정하지 않은 근무조에는 답하지 않는다.
 * 모든 통합 테스트가 같은 컨텍스트를 쓰도록 IntegrationTest가 늘 넣는다. 답을 정한 테스트(SolveRoundTripTest) 밖에서는 조용하다.
 */
@TestConfiguration(proxyBeanMethods = false)
public class FakeOptimizer {

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
