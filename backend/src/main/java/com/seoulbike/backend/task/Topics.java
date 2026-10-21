package com.seoulbike.backend.task;

import org.apache.kafka.clients.admin.NewTopic;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.kafka.config.TopicBuilder;

/** 계약: docs/specs/asyncapi.yaml. 키는 shiftId라서 같은 근무조 메시지는 순서대로 처리된다. */
@Configuration
public class Topics {

	public static final String AI_TASKS = "ai.tasks";
	public static final String AI_EVENTS = "ai.events";
	/** 에이전트 추론 기록 (관리자 로그). 최선 노력이라 재시도·dlq 토픽이 없다. backend가 DB에 남기므로 토픽은 하루만 둔다. */
	public static final String AI_TRACES = "ai.traces";

	@Bean
	NewTopic aiTasks(@Value("${app.kafka.partitions}") int partitions) {
		return TopicBuilder.name(AI_TASKS).partitions(partitions).build();
	}

	@Bean
	NewTopic aiEvents(@Value("${app.kafka.partitions}") int partitions) {
		return TopicBuilder.name(AI_EVENTS).partitions(partitions).build();
	}

	@Bean
	NewTopic aiTraces(@Value("${app.kafka.partitions}") int partitions) {
		return TopicBuilder.name(AI_TRACES).partitions(partitions).config("retention.ms", "86400000").build();
	}
}
