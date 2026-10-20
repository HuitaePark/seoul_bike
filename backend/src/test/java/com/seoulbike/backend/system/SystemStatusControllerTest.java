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
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.context.annotation.Import;
import org.springframework.test.web.servlet.MockMvc;

import com.seoulbike.backend.TestInfra;

/** 상태 API가 두 의존성을 실제로 확인하는지, 로그인 없이 열리는지 본다. */
@SpringBootTest(properties = TestInfra.PROPS)
@AutoConfigureMockMvc
@Import(TestInfra.class)
class SystemStatusControllerTest {

	@Autowired
	MockMvc mvc;

	@Test
	void reportsPostgisAndKafkaAsUpWithoutLogin() throws Exception {
		mvc.perform(get("/api/system/status"))
			.andExpect(status().isOk())
			.andExpect(jsonPath("$.dependencies", hasSize(2)))
			.andExpect(jsonPath("$.dependencies[*].status", everyItem(is("UP"))))
			.andExpect(jsonPath("$.dependencies[0].detail", containsString("PostGIS")))
			.andExpect(jsonPath("$.version", is("0.1.0")));
	}
}
