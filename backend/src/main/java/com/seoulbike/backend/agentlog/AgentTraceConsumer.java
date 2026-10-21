package com.seoulbike.backend.agentlog;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.kafka.annotation.KafkaListener;
import org.springframework.stereotype.Component;
import tools.jackson.core.JacksonException;
import tools.jackson.databind.json.JsonMapper;

import com.seoulbike.backend.task.Topics;

/**
 * ai.traces → agent_trace_events (관리자 로그). 계약 asyncapi v0.6.
 *
 * <p>ai.events와 일부러 다르게 다룬다. 최선 노력 기록이라 retry·dlq 토픽이 없다: 형식이 깨진 메시지는 기록만 하고 건너뛴다.
 * 컨슈머 그룹(backend-traces)과 리스너 스레드가 ai.events(backend-events)와 따로라서, 여기가 느리거나 막혀도 작업 진행 이벤트
 * 처리·작업 SSE·승인은 영향을 받지 않는다. DB 오류는 기본 오류 처리기가 몇 번 다시 하고 넘긴다.
 */
@Component
class AgentTraceConsumer {

	private static final Logger log = LoggerFactory.getLogger(AgentTraceConsumer.class);

	private final AgentTraceStore store;
	private final JsonMapper json;

	AgentTraceConsumer(AgentTraceStore store, JsonMapper json) {
		this.store = store;
		this.json = json;
	}

	@KafkaListener(topics = Topics.AI_TRACES, groupId = "backend-traces")
	void onTrace(String value) {
		try {
			store.insert(json.readTree(value));
		}
		catch (JacksonException | IllegalArgumentException e) {
			log.warn("형식이 맞지 않는 ai.traces 메시지를 건너뛴다: {} | {}", e.getMessage(),
					value.length() > 300 ? value.substring(0, 300) + "…" : value);
		}
	}
}
