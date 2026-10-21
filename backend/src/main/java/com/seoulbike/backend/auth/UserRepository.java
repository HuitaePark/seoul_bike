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
			AccountStatus status, Instant createdAt, Instant approvedAt, int tokenVersion) {

		CurrentUser toCurrentUser() {
			return new CurrentUser(id, name, role, truckId);
		}

		/** 계약 Account. 비밀번호 해시와 토큰 버전은 밖으로 내보내지 않는다. */
		Account toAccount() {
			return new Account(id, loginId, name, role, truckId, status, createdAt, approvedAt);
		}
	}

	/** approvedAt이 없으면 승인된 적 없는 계정(대기 중이거나 거절한 가입 신청)이다. */
	record Account(String id, String loginId, String name, Role role, String truckId, AccountStatus status,
			Instant createdAt, Instant approvedAt) {
	}

	/** 요청마다 토큰을 확인할 때 읽는 값. */
	record TokenState(AccountStatus status, int tokenVersion) {
	}

	private static final String COLUMNS = "id, login_id, password_hash, name, role, truck_id, status, created_at, approved_at, token_version";

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

	Optional<TokenState> tokenStateOf(String id) {
		return jdbc.sql("select status, token_version from users where id = :id")
			.param("id", id)
			.query((rs, row) -> new TokenState(AccountStatus.valueOf(rs.getString(1)), rs.getInt(2)))
			.optional();
	}

	int countPending() {
		return jdbc.sql("select count(*) from users where status = 'PENDING'").query(Integer.class).single();
	}

	/** 가입 신청을 한 줄로 세운다. 트랜잭션이 끝나면 풀린다. */
	void lockSignups() {
		jdbc.sql("select pg_advisory_xact_lock(hashtext('users.signup'))").query((rs, row) -> 1).single();
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

	/** 같은 login_id가 이미 있으면 넣지 않고 false. ACTIVE로 넣는 계정(관리자 발급, 시드)은 승인된 것으로 본다. */
	boolean insertIfAbsent(String id, String loginId, String passwordHash, String name, Role role, String truckId,
			AccountStatus status) {
		return jdbc.sql("""
				insert into users (id, login_id, password_hash, name, role, truck_id, status, approved_at)
				values (:id, :loginId, :passwordHash, :name, :role, :truckId, :status,
				        case when :status = 'ACTIVE' then now() end)
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

	/** 트럭 목록(ref.trucks, 합의 30)의 활성 트럭 ID를 번호 순으로. 기사 계정은 이 트럭만 가질 수 있다. */
	List<String> activeTruckIds() {
		return jdbc.sql("select truck_id from ref.trucks where active order by substring(truck_id from 4)::int")
			.query(String.class)
			.list();
	}

	/** PENDING일 때만 승인한다. 이미 처리된 신청이면 false. */
	boolean approve(String id, Role role, String truckId) {
		return jdbc.sql("""
				update users set status = 'ACTIVE', role = :role, truck_id = :truckId, approved_at = now()
				where id = :id and status = 'PENDING'
				""")
			.param("role", role.name())
			.param("truckId", truckId)
			.param("id", id)
			.update() == 1;
	}

	/** 토큰 버전을 올려 이미 발급된 토큰을 모두 무효로 한다. 다시 열어도 그 토큰은 돌아오지 않는다. */
	void disable(String id) {
		jdbc.sql("update users set status = 'DISABLED', token_version = token_version + 1 where id = :id")
			.param("id", id)
			.update();
	}

	/** 승인된 적 있는 DISABLED 계정만 다시 연다. 그사이 상태가 바뀌었으면 false. */
	boolean enable(String id) {
		return jdbc.sql("""
				update users set status = 'ACTIVE'
				where id = :id and status = 'DISABLED' and approved_at is not null
				""")
			.param("id", id)
			.update() == 1;
	}

	private static StoredUser map(ResultSet rs, int row) throws SQLException {
		OffsetDateTime createdAt = rs.getObject("created_at", OffsetDateTime.class);
		OffsetDateTime approvedAt = rs.getObject("approved_at", OffsetDateTime.class);
		return new StoredUser(rs.getString("id"), rs.getString("login_id"), rs.getString("password_hash"),
				rs.getString("name"), Role.valueOf(rs.getString("role")), rs.getString("truck_id"),
				AccountStatus.valueOf(rs.getString("status")), createdAt.toInstant(),
				approvedAt == null ? null : approvedAt.toInstant(), rs.getInt("token_version"));
	}
}
