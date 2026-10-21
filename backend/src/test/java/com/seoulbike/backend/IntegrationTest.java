package com.seoulbike.backend;

import java.lang.annotation.Documented;
import java.lang.annotation.ElementType;
import java.lang.annotation.Retention;
import java.lang.annotation.RetentionPolicy;
import java.lang.annotation.Target;

import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.context.annotation.Import;

import com.seoulbike.backend.plan.FakeOptimizer;

/**
 * 통합 테스트는 모두 이것만 붙인다. 설정이 하나라 Spring 컨텍스트도, PostGIS·Redpanda 컨테이너도 한 벌만 뜬다.
 * 테스트끼리 DB와 브로커를 같이 쓰므로 근무조·아이디·마커는 테스트마다 새로 만든다. @DirtiesContext로 풀지 않는다.
 */
@Target(ElementType.TYPE)
@Retention(RetentionPolicy.RUNTIME)
@Documented
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT, properties = TestInfra.PROPS)
@AutoConfigureMockMvc
@Import({ TestInfra.class, FakeOptimizer.class })
public @interface IntegrationTest {
}
