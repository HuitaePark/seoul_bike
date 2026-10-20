package com.seoulbike.backend.system;

import static org.hamcrest.Matchers.containsString;
import static org.hamcrest.Matchers.everyItem;
import static org.hamcrest.Matchers.hasSize;
import static org.hamcrest.Matchers.is;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.testcontainers.service.connection.ServiceConnection;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.test.web.servlet.MockMvc;
import org.testcontainers.junit.jupiter.Container;
import org.testcontainers.junit.jupiter.Testcontainers;
import org.testcontainers.postgresql.PostgreSQLContainer;
import org.testcontainers.redpanda.RedpandaContainer;
import org.testcontainers.utility.DockerImageName;

/** 운영과 같은 이미지(PostGIS, Redpanda)를 띄워 상태 API가 두 의존성을 실제로 확인하는지 본다. */
@SpringBootTest
@AutoConfigureMockMvc
@Testcontainers
class SystemStatusControllerTest {

	@Container
	@ServiceConnection
	static PostgreSQLContainer postgres = new PostgreSQLContainer(
			DockerImageName.parse("imresamu/postgis:16-3.4").asCompatibleSubstituteFor("postgres"));

	@Container
	@ServiceConnection
	static RedpandaContainer redpanda = new RedpandaContainer("redpandadata/redpanda:v26.2.5");

	@Autowired
	MockMvc mvc;

	@Test
	void reportsPostgisAndKafkaAsUp() throws Exception {
		mvc.perform(get("/api/system/status"))
			.andExpect(status().isOk())
			.andExpect(jsonPath("$.dependencies", hasSize(2)))
			.andExpect(jsonPath("$.dependencies[*].status", everyItem(is("UP"))))
			.andExpect(jsonPath("$.dependencies[0].detail", containsString("PostGIS")))
			.andExpect(jsonPath("$.version", is("0.1.0")));
	}
}
