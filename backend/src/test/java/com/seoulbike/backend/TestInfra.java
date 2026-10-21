package com.seoulbike.backend;

import org.springframework.boot.test.context.TestConfiguration;
import org.springframework.boot.testcontainers.service.connection.ServiceConnection;
import org.springframework.context.annotation.Bean;
import org.testcontainers.postgresql.PostgreSQLContainer;
import org.testcontainers.redpanda.RedpandaContainer;
import org.testcontainers.utility.DockerImageName;

/** 운영과 같은 이미지(PostGIS, Redpanda). 같은 설정을 쓰는 테스트는 컨테이너와 컨텍스트를 함께 쓴다. */
@TestConfiguration(proxyBeanMethods = false)
public class TestInfra {

	public static final String PROPS = """
			app.auth.jwt-secret=test-secret-that-is-at-least-32-bytes-long
			app.auth.seed-password=test-password
			app.fake-agent.enabled=true
			app.fake-agent.step-millis=5
			app.internal.token=test-internal-token
			app.kafka.retry.delay=200
			app.solve.timeout=5s
			app.auth.login-max-failures=3
			app.auth.max-pending-signups=5
			""";

	@Bean
	@ServiceConnection
	PostgreSQLContainer postgres() {
		return new PostgreSQLContainer(
				DockerImageName.parse("imresamu/postgis:16-3.4").asCompatibleSubstituteFor("postgres"));
	}

	@Bean
	@ServiceConnection
	RedpandaContainer redpanda() {
		return new RedpandaContainer("redpandadata/redpanda:v26.2.5");
	}
}
