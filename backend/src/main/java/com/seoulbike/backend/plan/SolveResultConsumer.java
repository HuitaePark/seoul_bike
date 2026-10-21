package com.seoulbike.backend.plan;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.kafka.annotation.BackOff;
import org.springframework.kafka.annotation.DltHandler;
import org.springframework.kafka.annotation.KafkaListener;
import org.springframework.kafka.annotation.RetryableTopic;
import org.springframework.kafka.retrytopic.DltStrategy;
import org.springframework.kafka.retrytopic.SameIntervalTopicReuseStrategy;
import org.springframework.kafka.support.KafkaHeaders;
import org.springframework.messaging.handler.annotation.Header;
import org.springframework.stereotype.Component;
import tools.jackson.core.JacksonException;
import tools.jackson.databind.json.JsonMapper;

/**
 * solve.results → 요청 행에 저장 → 기다리는 요청(SolveGateway)에 넘긴다. 저장 트랜잭션이 끝난 뒤에 넘긴다.
 *
 * <p>처리하지 못한 메시지는 버리지 않는다 (계약 asyncapi "`<토픽>.retry` → `<토픽>.dlq`").
 * DB 장애나 아직 모르는 요청처럼 다시 하면 될 수 있는 실패는 solve.results.retry로 몇 번 다시 하고, 그래도 안 되면 solve.results.dlq.
 * 형식이 깨진 메시지는 다시 해도 같으므로 바로 solve.results.dlq로 보낸다.
 */
@Component
class SolveResultConsumer {

	private static final Logger log = LoggerFactory.getLogger(SolveResultConsumer.class);

	private final SolveResultStore store;
	private final SolveGateway gateway;
	private final JsonMapper json;

	SolveResultConsumer(SolveResultStore store, SolveGateway gateway, JsonMapper json) {
		this.store = store;
		this.gateway = gateway;
		this.json = json;
	}

	@RetryableTopic(attempts = "${app.kafka.retry.attempts}", backOff = @BackOff(delayString = "${app.kafka.retry.delay}"),
			sameIntervalTopicReuseStrategy = SameIntervalTopicReuseStrategy.SINGLE_TOPIC, retryTopicSuffix = ".retry",
			dltTopicSuffix = ".dlq", numPartitions = "${app.kafka.partitions}",
			exclude = { JacksonException.class, IllegalArgumentException.class }, traversingCauses = "true",
			// dlq 처리기가 실패해도 그 메시지를 계속 다시 돌리지 않는다 (기본값은 무한 반복).
			dltStrategy = DltStrategy.FAIL_ON_ERROR)
	@KafkaListener(topics = SolveTopics.SOLVE_RESULTS, groupId = "backend-solve")
	void onResult(String value) {
		store.store(json.readTree(value)).ifPresent(gateway::complete);
	}

	/** dlq에 남은 메시지를 알린다. 여기서 실패하면 안 되므로 헤더가 없어도 받는다. */
	@DltHandler
	void onDeadLetter(String value, @Header(name = KafkaHeaders.EXCEPTION_MESSAGE, required = false) String reason) {
		log.error("solve.results 메시지를 처리하지 못해 solve.results.dlq에 남겼다: {} | {}", reason,
				value.length() > 300 ? value.substring(0, 300) + "…" : value);
	}
}
