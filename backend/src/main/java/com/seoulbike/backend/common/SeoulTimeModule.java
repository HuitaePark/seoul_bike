package com.seoulbike.backend.common;

import java.time.Instant;
import java.time.OffsetDateTime;
import java.time.ZonedDateTime;
import java.util.function.Function;

import org.springframework.stereotype.Component;
import tools.jackson.core.JsonGenerator;
import tools.jackson.databind.SerializationContext;
import tools.jackson.databind.module.SimpleModule;
import tools.jackson.databind.ser.std.StdSerializer;

/**
 * 공용 JsonMapper(REST 응답·Kafka 메시지·SSE 모두 이것으로 쓴다)가 시각을 +09:00으로 쓰게 한다.
 * 레코드마다 형식을 달지 않고 여기 한 곳에서 정한다. Spring Boot가 JacksonModule 빈을 자동 등록한다.
 */
@Component
class SeoulTimeModule extends SimpleModule {

	SeoulTimeModule() {
		super("seoul-time");
		addSerializer(Instant.class, serializer(Instant.class, t -> t));
		addSerializer(OffsetDateTime.class, serializer(OffsetDateTime.class, OffsetDateTime::toInstant));
		addSerializer(ZonedDateTime.class, serializer(ZonedDateTime.class, ZonedDateTime::toInstant));
	}

	private static <T> StdSerializer<T> serializer(Class<T> type, Function<T, Instant> instant) {
		return new StdSerializer<>(type) {
			@Override
			public void serialize(T value, JsonGenerator gen, SerializationContext ctxt) {
				gen.writeString(SeoulTime.format(instant.apply(value)));
			}
		};
	}

}
