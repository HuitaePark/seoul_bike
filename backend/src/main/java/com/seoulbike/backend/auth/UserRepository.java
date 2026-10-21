package com.seoulbike.backend.auth;

import java.sql.ResultSet;
import java.sql.SQLException;
import java.time.Instant;
import java.time.OffsetDateTime;
import java.util.List;
import java.util.Optional;

import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Repository;

@Repository
class UserRepository {

	record StoredUser(String id, String loginId, String passwordHash, String name, Role role, String truckId,
			AccountStatus status, Instant createdAt) {

		CurrentUser toCurrentUser() {
			return new CurrentUser(id, name, role, truckId);
		}

		/** 계약 Account. 비밀번호 해시는 밖으로 내보내지 않는다. */
		Account toAccount() {
			return new Account(id, loginId, name, role, truckId, status, createdAt);
		}
	}

	record Account(String id, String loginId, String name, Role role, String truckId, AccountStatus status,
			Instant createdAt) {
	}

	private static final String COLUMNS = "id, login_id, password_hash, name, role, truck_id, status, created_at";

	private final JdbcClient jdbc;

	UserRepository(JdbcClient jdbc) {
		this.jdbc = jdbc;
	}

	Optional<StoredUser> findByLoginId(String loginId) {
		return jdbc.sql("select " + COLUMNS + " from users where login_id = :loginId")
			.param("loginId", loginId)
			.query(UserRepository::map)
			.optional();
	}

	Optional<StoredUser> findById(String id) {
		return jdbc.sql("select " + COLUMNS + " from users where id = :id").param("id", id).query(UserRepository::map).optional();
	}

	Optional<AccountStatus> statusOf(String id) {
		return jdbc.sql("select status from users where id = :id")
			.param("id", id)
			.query((rs, row) -> AccountStatus.valueOf(rs.getString(1)))
			.optional();
	}

	int countPending() {
		return jdbc.sql("select count(*) from users where status = 'PENDING'").query(Integer.class).single();
	}

	List<StoredUser> list(AccountStatus status) {
		return jdbc.sql("select " + COLUMNS + """
				 from users where (cast(:status as varchar) is null or status = :status)
				 order by created_at desc
				""")
			.param("status", status == null ? null : status.name())
			.query(UserRepository::map)
			.list();
	}

	/** 같은 login_id가 이미 있으면 넣지 않고 false. */
	boolean insertIfAbsent(String id, String loginId, String passwordHash, String name, Role role, String truckId,
			AccountStatus status) {
		return jdbc.sql("""
				insert into users (id, login_id, password_hash, name, role, truck_id, status)
				values (:id, :loginId, :passwordHash, :name, :role, :truckId, :status)
				on conflict (login_id) do nothing
				""")
			.param("id", id)
			.param("loginId", loginId)
			.param("passwordHash", passwordHash)
			.param("name", name)
			.param("role", role.name())
			.param("truckId", truckId)
			.param("status", status.name())
			.update() == 1;
	}

	/** PENDING일 때만 승인한다. 이미 처리된 신청이면 false. */
	boolean approve(String id, Role role, String truckId) {
		return jdbc.sql("""
				update users set status = 'ACTIVE', role = :role, truck_id = :truckId
				where id = :id and status = 'PENDING'
				""")
			.param("role", role.name())
			.param("truckId", truckId)
			.param("id", id)
			.update() == 1;
	}

	void disable(String id) {
		jdbc.sql("update users set status = 'DISABLED' where id = :id").param("id", id).update();
	}

	private static StoredUser map(ResultSet rs, int row) throws SQLException {
		OffsetDateTime createdAt = rs.getObject("created_at", OffsetDateTime.class);
		return new StoredUser(rs.getString("id"), rs.getString("login_id"), rs.getString("password_hash"),
				rs.getString("name"), Role.valueOf(rs.getString("role")), rs.getString("truck_id"),
				AccountStatus.valueOf(rs.getString("status")), createdAt.toInstant());
	}
}
