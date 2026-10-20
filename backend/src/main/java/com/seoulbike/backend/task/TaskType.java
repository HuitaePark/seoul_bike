package com.seoulbike.backend.task;

/** 계약 TaskType. RESUME은 Kafka 명령에만 있고 화면에서 만들 수 없다. */
public enum TaskType {
	PLAN_DRAFT, FIELD_REPORT, REQUEST, QUESTION
}
