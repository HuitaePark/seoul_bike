package com.seoulbike.backend;

import java.nio.ByteBuffer;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.time.Instant;
import java.util.List;
import java.util.Map;
import java.util.UUID;

import org.apache.kafka.clients.consumer.ConsumerConfig;
import org.apache.kafka.clients.consumer.ConsumerRecord;
import org.apache.kafka.clients.consumer.KafkaConsumer;
import org.apache.kafka.clients.producer.ProducerConfig;
import org.apache.kafka.common.header.Header;
import org.apache.kafka.common.serialization.StringDeserializer;
import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.kafka.retrytopic.RetryTopicHeaders;

/** dlq에 남은 메시지를 찾아 확인한다. 테스트마다 새 그룹으로 처음부터 읽는다. */
public final class DeadLetters {

	private DeadLetters() {
	}

	/** marker를 담은 메시지가 dlq에 올 때까지 30초 기다린다. */
	public static ConsumerRecord<String, String> await(KafkaTemplate<String, String> kafka, String dlq, String marker) {
		Object servers = kafka.getProducerFactory().getConfigurationProperties().get(ProducerConfig.BOOTSTRAP_SERVERS_CONFIG);
		try (var consumer = new KafkaConsumer<>(Map.<String, Object>of(ConsumerConfig.BOOTSTRAP_SERVERS_CONFIG, servers,
				ConsumerConfig.GROUP_ID_CONFIG, "test-" + UUID.randomUUID(), ConsumerConfig.AUTO_OFFSET_RESET_CONFIG, "earliest"),
				new StringDeserializer(), new StringDeserializer())) {
			consumer.subscribe(List.of(dlq));
			Instant deadline = Instant.now().plusSeconds(30);
			while (Instant.now().isBefore(deadline)) {
				for (ConsumerRecord<String, String> r : consumer.poll(Duration.ofMillis(500))) {
					if (r.value() != null && r.value().contains(marker)) {
						return r;
					}
				}
			}
		}
		throw new AssertionError(dlq + "에서 " + marker + "를 찾지 못했다");
	}

	public static String header(ConsumerRecord<String, String> r, String name) {
		Header h = r.headers().lastHeader(name);
		return h == null ? null : new String(h.value(), StandardCharsets.UTF_8);
	}

	/** 처리를 몇 번 시도했는가. dlq로 보낼 때 attempts 헤더가 한 번 더 올라가므로 1을 뺀다. */
	public static int tries(ConsumerRecord<String, String> r) {
		return ByteBuffer.wrap(r.headers().lastHeader(RetryTopicHeaders.DEFAULT_HEADER_ATTEMPTS).value()).getInt() - 1;
	}
}
