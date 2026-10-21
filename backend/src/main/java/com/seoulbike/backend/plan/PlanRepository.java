package com.seoulbike.backend.plan;

import java.util.Optional;

import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Repository;

@Repository
class PlanRepository {

	/** pg_advisory_xact_lock의 첫 키. 근무조 잠금을 다른 용도의 advisory lock과 나눈다. */
	private static final int SHIFT_LOCK_CLASS = 7;

	/** 활성 계획. routes는 계약 Route 배열(JSON)이다. */
	record Active(int version, String routesJson) {
	}

	private final JdbcClient jdbc;

	PlanRepository(JdbcClient jdbc) {
		this.jdbc = jdbc;
	}

	/**
	 * 근무조 단위로 줄을 세운다 (트랜잭션이 끝나면 풀린다). 요청 seq와 계획 version은 max+1이라
	 * 같은 근무조에서 동시에 매기면 겹친다. 행이 아직 없을 수 있어 행 잠금 대신 advisory lock을 쓴다.
	 */
	void lockShift(String shiftId) {
		jdbc.sql("select 1 from (select pg_advisory_xact_lock(:lockClass, hashtext(:shiftId))) l")
			.param("lockClass", SHIFT_LOCK_CLASS)
			.param("shiftId", shiftId)
			.query(Integer.class)
			.single();
	}

	Optional<Active> active(String shiftId) {
		return jdbc.sql("select version, routes::text as routes from plans where shift_id = :shiftId and status = 'ACTIVE'")
			.param("shiftId", shiftId)
			.query((rs, i) -> new Active(rs.getInt("version"), rs.getString("routes")))
			.optional();
	}

	int nextVersion(String shiftId) {
		return jdbc.sql("select coalesce(max(version), 0) + 1 from plans where shift_id = :shiftId")
			.param("shiftId", shiftId)
			.query(Integer.class)
			.single();
	}

	void insertCandidate(String planId, String shiftId, int version, String routesJson, String unservedJson,
			String requestId) {
		jdbc.sql("""
				insert into plans (plan_id, shift_id, version, status, provisional, routes, unserved, request_id)
				values (:planId, :shiftId, :version, 'PROPOSED', false, cast(:routes as jsonb), cast(:unserved as jsonb), :requestId)
				""")
			.param("planId", planId)
			.param("shiftId", shiftId)
			.param("version", version)
			.param("routes", routesJson)
			.param("unserved", unservedJson)
			.param("requestId", requestId)
			.update();
	}
}
