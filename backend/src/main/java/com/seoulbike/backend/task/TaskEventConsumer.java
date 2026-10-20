package com.seoulbike.backend.task;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.kafka.annotation.KafkaListener;
import org.springframework.stereotype.Component;
import tools.jackson.core.JacksonException;
import tools.jackson.databind.json.JsonMapper;

/** ai.events → DB 저장 → SSE. 저장 트랜잭션이 끝난 뒤에 화면으로 보낸다. */
@Component
class TaskEventConsumer {

	private static final Logger log = LoggerFactory.getLogger(TaskEventConsumer.class);

	private final TaskEventStore store;
	private final SseHub hub;
	private final JsonMapper json;

	TaskEventConsumer(TaskEventStore store, SseHub hub, JsonMapper json) {
		this.store = store;
		this.hub = hub;
		this.json = json;
	}

	@KafkaListener(topics = Topics.AI_EVENTS, groupId = "backend-events")
	void onEvent(String value) {
		try {
			store.store(json.readTree(value)).ifPresent(hub::publish);
		}
		catch (JacksonException | IllegalArgumentException e) {
			// 형식이 깨진 메시지는 재시도해도 같다. 기록만 남기고 넘어간다 (재시도·DLQ는 M4).
			log.error("ai.events 메시지를 읽지 못했다: {}", e.getMessage());
		}
	}
}
