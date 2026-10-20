package com.seoulbike.backend.system;

import java.time.Instant;
import java.util.HashMap;
import java.util.List;
import java.util.Set;
import java.util.concurrent.TimeUnit;

import org.apache.kafka.clients.admin.AdminClient;
import org.apache.kafka.clients.admin.AdminClientConfig;
import org.springframework.beans.factory.ObjectProvider;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.info.BuildProperties;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.kafka.core.KafkaAdmin;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * 통합 데모용 시스템 상태. backend가 docker network 안에서 의존하는 저장소와 대기열에
 * 실제로 붙는지 확인한다. vectordb는 rag-mcp 소유라 여기서 보지 않는다.
 */
@RestController
class SystemStatusController {

	private static final long CHECK_TIMEOUT_SECONDS = 3;

	private final JdbcTemplate jdbc;
	private final KafkaAdmin kafkaAdmin;
	private final BuildProperties build;
	private final String revision;

	SystemStatusController(JdbcTemplate jdbc, KafkaAdmin kafkaAdmin, ObjectProvider<BuildProperties> build,
			@Value("${app.revision}") String revision) {
		this.jdbc = jdbc;
		this.kafkaAdmin = kafkaAdmin;
		this.build = build.getIfAvailable();
		this.revision = revision;
	}

	@GetMapping("/api/system/status")
	SystemStatus status() {
		return new SystemStatus("seoul-bike-backend", build == null ? "dev" : build.getVersion(), revision,
				build == null ? null : build.getTime(), Instant.now(), List.of(postgres(), kafka()));
	}

	private DependencyStatus postgres() {
		try {
			String postgis = jdbc.queryForObject("select postgis_lib_version()", String.class);
			String server = jdbc.queryForObject("show server_version", String.class);
			return DependencyStatus.up("PostgreSQL", "PostgreSQL " + server + " · PostGIS " + postgis);
		}
		catch (RuntimeException e) {
			return DependencyStatus.down("PostgreSQL", e);
		}
	}

	private DependencyStatus kafka() {
		var config = new HashMap<>(kafkaAdmin.getConfigurationProperties());
		config.put(AdminClientConfig.REQUEST_TIMEOUT_MS_CONFIG, (int) TimeUnit.SECONDS.toMillis(CHECK_TIMEOUT_SECONDS));
		config.put(AdminClientConfig.DEFAULT_API_TIMEOUT_MS_CONFIG, (int) TimeUnit.SECONDS.toMillis(CHECK_TIMEOUT_SECONDS));
		try (AdminClient admin = AdminClient.create(config)) {
			int brokers = admin.describeCluster().nodes().get(CHECK_TIMEOUT_SECONDS, TimeUnit.SECONDS).size();
			Set<String> topics = admin.listTopics().names().get(CHECK_TIMEOUT_SECONDS, TimeUnit.SECONDS);
			return DependencyStatus.up("Kafka (Redpanda)", "브로커 " + brokers + " · 토픽 " + topics.size());
		}
		catch (Exception e) {
			if (e instanceof InterruptedException) {
				Thread.currentThread().interrupt();
			}
			return DependencyStatus.down("Kafka (Redpanda)", e);
		}
	}

	record SystemStatus(String service, String version, String revision, Instant builtAt, Instant serverTime,
			List<DependencyStatus> dependencies) {
	}

	record DependencyStatus(String name, String status, String detail) {

		static DependencyStatus up(String name, String detail) {
			return new DependencyStatus(name, "UP", detail);
		}

		static DependencyStatus down(String name, Exception e) {
			Throwable root = e;
			while (root.getCause() != null) {
				root = root.getCause();
			}
			return new DependencyStatus(name, "DOWN", root.getClass().getSimpleName() + ": " + root.getMessage());
		}
	}
}
