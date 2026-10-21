package com.seoulbike.backend.task;

import static org.assertj.core.api.Assertions.assertThat;

import java.util.UUID;
import java.util.concurrent.TimeUnit;

import org.apache.kafka.clients.consumer.ConsumerRecord;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.context.annotation.Import;
import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.kafka.support.KafkaHeaders;
import tools.jackson.databind.json.JsonMapper;

import com.seoulbike.backend.DeadLetters;
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

		ConsumerRecord<String, String> dead = DeadLetters.await(kafka, DLQ, marker);
		assertThat(DeadLetters.header(dead, KafkaHeaders.ORIGINAL_TOPIC)).isEqualTo(Topics.AI_EVENTS);
		assertThat(DeadLetters.tries(dead)).isEqualTo(1);
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

		ConsumerRecord<String, String> dead = DeadLetters.await(kafka, DLQ, eventId);
		assertThat(DeadLetters.tries(dead)).isEqualTo(4);
		assertThat(DeadLetters.header(dead, KafkaHeaders.EXCEPTION_MESSAGE)).contains("모르는 작업");
	}
}
