package com.seoulbike.backend.auth;

import java.util.Optional;

import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Repository;

@Repository
class UserRepository {

	record StoredUser(String id, String loginId, String passwordHash, String name, Role role, String truckId) {

		CurrentUser toCurrentUser() {
			return new CurrentUser(id, name, role, truckId);
		}
	}

	private final JdbcClient jdbc;

	UserRepository(JdbcClient jdbc) {
		this.jdbc = jdbc;
	}

	Optional<StoredUser> findByLoginId(String loginId) {
		return jdbc.sql("select id, login_id, password_hash, name, role, truck_id from users where login_id = :loginId")
			.param("loginId", loginId)
			.query((rs, i) -> new StoredUser(rs.getString("id"), rs.getString("login_id"), rs.getString("password_hash"),
					rs.getString("name"), Role.valueOf(rs.getString("role")), rs.getString("truck_id")))
			.optional();
	}

	/** 같은 login_id가 이미 있으면 건드리지 않는다. 시드 계정을 여러 번 올려도 안전하다. */
	boolean insertIfAbsent(StoredUser user) {
		return jdbc.sql("""
				insert into users (id, login_id, password_hash, name, role, truck_id)
				values (:id, :loginId, :passwordHash, :name, :role, :truckId)
				on conflict (login_id) do nothing
				""")
			.param("id", user.id())
			.param("loginId", user.loginId())
			.param("passwordHash", user.passwordHash())
			.param("name", user.name())
			.param("role", user.role().name())
			.param("truckId", user.truckId())
			.update() == 1;
	}
}
