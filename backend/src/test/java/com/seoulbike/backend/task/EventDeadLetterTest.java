package com.seoulbike.backend.task;

import static org.assertj.core.api.Assertions.assertThat;

import java.nio.ByteBuffer;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.time.Instant;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.TimeUnit;

import org.apache.kafka.clients.consumer.ConsumerConfig;
import org.apache.kafka.clients.consumer.ConsumerRecord;
import org.apache.kafka.clients.consumer.KafkaConsumer;
import org.apache.kafka.clients.producer.ProducerConfig;
import org.apache.kafka.common.header.Header;
import org.apache.kafka.common.serialization.StringDeserializer;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.context.annotation.Import;
import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.kafka.retrytopic.RetryTopicHeaders;
import org.springframework.kafka.support.KafkaHeaders;
import tools.jackson.databind.json.JsonMapper;

import com.seoulbike.backend.TestInfra;

/**
 * ai.events를 처리하지 못하면 버리지 않고 dlq에 남긴다. 다시 해도 같은 형식 오류는 재시도 없이 바로 가고,
 * 다시 하면 될 수 있는 실패(모르는 작업)는 ai.events.retry를 거친 뒤 간다 (TestInfra: 4번, 200ms 간격).
 */
@SpringBootTest(properties = TestInfra.PROPS)
@Import(TestInfra.class)
class EventDeadLetterTest {

	private static final String DLQ = Topics.AI_EVENTS + ".dlq";

	@Autowired
	KafkaTemplate<String, String> kafka;

	@Autowired
	JsonMapper json;

	@Test
	void malformedEventGoesStraightToDlq() throws Exception {
		String marker = "not-json-" + UUID.randomUUID();
		kafka.send(Topics.AI_EVENTS, "s-dlq", marker).get(10, TimeUnit.SECONDS);

		ConsumerRecord<String, String> dead = awaitDeadLetter(marker);
		assertThat(header(dead, KafkaHeaders.ORIGINAL_TOPIC)).isEqualTo(Topics.AI_EVENTS);
		assertThat(tries(dead)).isEqualTo(1);
	}

	@Test
	void eventOfUnknownTaskIsRetriedThenDeadLettered() throws Exception {
		String eventId = UUID.randomUUID().toString();
		String event = json.writeValueAsString(json.createObjectNode()
			.put("eventId", eventId)
			.put("schemaVersion", 1)
			.put("shiftId", "s-dlq")
			.put("taskId", UUID.randomUUID().toString())
			.put("type", "TOKEN")
			.put("token", "x"));
		kafka.send(Topics.AI_EVENTS, "s-dlq", event).get(10, TimeUnit.SECONDS);

		ConsumerRecord<String, String> dead = awaitDeadLetter(eventId);
		assertThat(tries(dead)).isEqualTo(4);
		assertThat(header(dead, KafkaHeaders.EXCEPTION_MESSAGE)).contains("모르는 작업");
	}

	private ConsumerRecord<String, String> awaitDeadLetter(String marker) {
		Object servers = kafka.getProducerFactory().getConfigurationProperties().get(ProducerConfig.BOOTSTRAP_SERVERS_CONFIG);
		try (var consumer = new KafkaConsumer<>(Map.<String, Object>of(ConsumerConfig.BOOTSTRAP_SERVERS_CONFIG, servers,
				ConsumerConfig.GROUP_ID_CONFIG, "test-" + UUID.randomUUID(), ConsumerConfig.AUTO_OFFSET_RESET_CONFIG, "earliest"),
				new StringDeserializer(), new StringDeserializer())) {
			consumer.subscribe(List.of(DLQ));
			Instant deadline = Instant.now().plusSeconds(30);
			while (Instant.now().isBefore(deadline)) {
				for (ConsumerRecord<String, String> r : consumer.poll(Duration.ofMillis(500))) {
					if (r.value() != null && r.value().contains(marker)) {
						return r;
					}
				}
			}
		}
		throw new AssertionError(DLQ + "에서 " + marker + "를 찾지 못했다");
	}

	private static String header(ConsumerRecord<String, String> r, String name) {
		Header h = r.headers().lastHeader(name);
		return h == null ? null : new String(h.value(), StandardCharsets.UTF_8);
	}

	/** 처리를 몇 번 시도했는가. dlq로 보낼 때 attempts 헤더가 한 번 더 올라가므로 1을 뺀다. */
	private static int tries(ConsumerRecord<String, String> r) {
		return ByteBuffer.wrap(r.headers().lastHeader(RetryTopicHeaders.DEFAULT_HEADER_ATTEMPTS).value()).getInt() - 1;
	}
}
