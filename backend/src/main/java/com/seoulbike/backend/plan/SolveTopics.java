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

	@Bean
	NewTopic solveRequests(@Value("${app.kafka.partitions}") int partitions) {
		return TopicBuilder.name(SOLVE_REQUESTS).partitions(partitions).build();
	}

	@Bean
	NewTopic solveResults(@Value("${app.kafka.partitions}") int partitions) {
		return TopicBuilder.name(SOLVE_RESULTS).partitions(partitions).build();
	}
}
