package com.seoulbike.backend.auth;

import static org.assertj.core.api.Assertions.assertThat;

import java.util.Map;
import java.util.UUID;
import java.util.stream.Collectors;

import org.flywaydb.core.Flyway;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.jdbc.datasource.DriverManagerDataSource;
import org.testcontainers.postgresql.PostgreSQLContainer;

import com.seoulbike.backend.IntegrationTest;

/**
 * V12가 운영 DB처럼 truck-N 기사가 있는 DB에서 트럭 ID를 TR-NN으로 옮기는지 (합의 30).
 * 공유 DB는 이미 V12까지 올라가 있으므로 같은 컨테이너에 빈 DB를 하나 더 만들어 V11까지 올리고, 행을 넣은 뒤 V12를 돌린다.
 */
@IntegrationTest
class TruckIdMigrationTest {

	@Autowired
	PostgreSQLContainer postgres;

	@Autowired
	JdbcClient jdbc;

	@Test
	void 기사_트럭_truck_N은_TR_NN이_되고_그_계정의_토큰은_무효가_되며_다른_값은_그대로_남는다() {
		String db = "mig_" + UUID.randomUUID().toString().replace("-", "").substring(0, 12);
		jdbc.sql("create database " + db).update();
		try {
			var source = new DriverManagerDataSource(
					"jdbc:postgresql://" + postgres.getHost() + ":" + postgres.getMappedPort(5432) + "/" + db,
					postgres.getUsername(), postgres.getPassword());
			Flyway.configure().dataSource(source).target("11").load().migrate();
			var old = JdbcClient.create(source);
			old.sql("""
					insert into users (id, login_id, password_hash, name, role, truck_id, status, token_version) values
					('u-1', 'd1', 'x', '기사', 'DRIVER', 'truck-1', 'ACTIVE', 0),
					('u-2', 'd2', 'x', '기사', 'DRIVER', 'truck-12', 'DISABLED', 1),
					('u-3', 'd3', 'x', '기사', 'DRIVER', 'truck-007', 'ACTIVE', 0),
					('u-4', 'd4', 'x', '기사', 'DRIVER', 'truck-123', 'ACTIVE', 0),
					('u-5', 'd5', 'x', '기사', 'DRIVER', '12가3456', 'ACTIVE', 0),
					('u-6', 'd6', 'x', '기사', 'DRIVER', 'TR-05', 'ACTIVE', 0),
					('u-7', 'm1', 'x', '관리자', 'MANAGER', null, 'ACTIVE', 0)
					""").update();

			Flyway.configure().dataSource(source).load().migrate();

			Map<String, String> after = old.sql("select id, truck_id, token_version from users order by id")
				.query((rs, i) -> Map.entry(rs.getString("id"), rs.getString("truck_id") + "/" + rs.getInt("token_version")))
				.list()
				.stream()
				.collect(Collectors.toMap(Map.Entry::getKey, Map.Entry::getValue));
			// 바뀐 계정은 토큰 버전이 하나 오른다: 예전 truckId가 든 토큰은 401이 되고 다시 로그인해 TR- 트럭을 받는다.
			assertThat(after).containsEntry("u-1", "TR-01/1")
				.containsEntry("u-2", "TR-12/2")
				.containsEntry("u-3", "TR-07/1")
				.containsEntry("u-4", "TR-123/1")
				// truck-N 꼴이 아닌 값과 이미 TR-인 값은 건드리지 않는다 (시작할 때 경고, 관리자가 고친다).
				.containsEntry("u-5", "12가3456/0")
				.containsEntry("u-6", "TR-05/0")
				.containsEntry("u-7", "null/0");
			// 트럭 행은 로더가 채운다. 새 DB에는 구역 행이 없어 마이그레이션이 채울 수 없다.
			assertThat(old.sql("select count(*) from ref.trucks").query(Integer.class).single()).isZero();
		}
		finally {
			jdbc.sql("drop database if exists " + db + " with (force)").update();
		}
	}
}
