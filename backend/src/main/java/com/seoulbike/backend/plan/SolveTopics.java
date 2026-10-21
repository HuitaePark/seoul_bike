package com.seoulbike.backend.plan;

import org.apache.kafka.clients.admin.NewTopic;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.kafka.config.TopicBuilder;

/** 계약: docs/specs/asyncapi.yaml. 키는 shiftId라서 같은 근무조의 요청과 결과는 순서대로 처리된다. */
@Configuration
public class SolveTopics {

	public static final String SOLVE_REQUESTS = "solve.requests";
	public static final String SOLVE_RESULTS = "solve.results";
	/** optimizer worker의 재시도·DLQ (계약 공통 규칙). worker는 토픽을 자동으로 만들지 않고 이 둘을 구독·발행하므로 backend가 만든다. */
	public static final String SOLVE_REQUESTS_RETRY = SOLVE_REQUESTS + ".retry";
	public static final String SOLVE_REQUESTS_DLQ = SOLVE_REQUESTS + ".dlq";

	@Bean
	NewTopic solveRequests(@Value("${app.kafka.partitions}") int partitions) {
		return TopicBuilder.name(SOLVE_REQUESTS).partitions(partitions).build();
	}

	@Bean
	NewTopic solveResults(@Value("${app.kafka.partitions}") int partitions) {
		return TopicBuilder.name(SOLVE_RESULTS).partitions(partitions).build();
	}

	@Bean
	NewTopic solveRequestsRetry(@Value("${app.kafka.partitions}") int partitions) {
		return TopicBuilder.name(SOLVE_REQUESTS_RETRY).partitions(partitions).build();
	}

	@Bean
	NewTopic solveRequestsDlq(@Value("${app.kafka.partitions}") int partitions) {
		return TopicBuilder.name(SOLVE_REQUESTS_DLQ).partitions(partitions).build();
	}
}
