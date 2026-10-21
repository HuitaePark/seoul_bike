package com.seoulbike.backend.plan;

import java.sql.ResultSet;
import java.sql.SQLException;
import java.util.Optional;
import java.util.UUID;

import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Repository;

@Repository
class PlanRepository {

	/** pg_advisory_xact_lock의 첫 키. 근무조 잠금을 다른 용도의 advisory lock과 나눈다. */
	private static final int SHIFT_LOCK_CLASS = 7;

	private static final String COLUMNS = """
			plan_id, shift_id, version, status, provisional, routes::text as routes, unserved::text as unserved, explanation,
			request_id, activated_by_approval, applied_candidate_id, applied_truck_id""";

	/** 활성 계획. routes는 계약 Route 배열(JSON)이다. */
	record Active(int version, String routesJson) {
	}

	/**
	 * 계획 한 행. routes·unserved는 계약 Route·Unserved 배열(JSON)이다.
	 *
	 * @param activatedByApproval 이 행을 ACTIVE로 만든 승인 (V8). 아직 활성화된 적 없으면 null
	 * @param appliedCandidateId 경로 적용으로 만든 버전이면 옮겨 온 후보. 아니면 null
	 */
	record Plan(String planId, String shiftId, int version, String status, boolean provisional, String routesJson,
			String unservedJson, String explanation, String requestId, UUID activatedByApproval,
			String appliedCandidateId, String appliedTruckId) {
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

	Optional<Plan> activePlan(String shiftId) {
		return one("select " + COLUMNS + " from plans where shift_id = :shiftId and status = 'ACTIVE'", "shiftId", shiftId);
	}

	/** 상태와 상관없이 가장 큰 version (공개 API "가장 최근 버전의 계획"). */
	Optional<Plan> latest(String shiftId) {
		return one("select " + COLUMNS + " from plans where shift_id = :shiftId order by version desc limit 1", "shiftId",
				shiftId);
	}

	Optional<Plan> find(String planId) {
		return one("select " + COLUMNS + " from plans where plan_id = :planId", "planId", planId);
	}

	/** 이 승인으로 ACTIVE가 된 계획. 그 뒤 다른 버전에 밀려 CLOSED여도 찾는다 (재시도 판단용). */
	Optional<Plan> activatedBy(UUID approvalId) {
		return jdbc.sql("select " + COLUMNS + " from plans where activated_by_approval = :approvalId")
			.param("approvalId", approvalId)
			.query(PlanRepository::map)
			.optional();
	}

	/**
	 * 이 트럭이 들어 있는 ACTIVE 계획 중 가장 늦게 활성화된 것의 그 트럭 경로(계약 Route JSON).
	 * 기사 계정에는 근무조가 없어 트럭으로만 찾는다. 근무조마다 ACTIVE가 하나이므로 최근 근무조의 경로가 나온다.
	 */
	Optional<String> activeRouteOf(String truckId) {
		return jdbc.sql("""
				select r.route::text
				from plans p cross join lateral jsonb_array_elements(p.routes) as r(route)
				where p.status = 'ACTIVE' and r.route ->> 'truckId' = :truckId
				order by p.activated_at desc nulls last, p.created_at desc
				limit 1
				""")
			.param("truckId", truckId)
			.query(String.class)
			.optional();
	}

	/** 후보를 만든 계산 요청이 기준으로 삼은 활성 version (요청 당시 활성 계획이 없었으면 0). */
	Optional<Integer> basedOnVersion(String requestId) {
		return jdbc.sql("select plan_version from solve_requests where request_id = :requestId")
			.param("requestId", requestId)
			.query(Integer.class)
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

	/** 지금 ACTIVE를 닫는다. 근무조마다 ACTIVE는 하나(plans_one_active_idx)라 새 ACTIVE보다 먼저 부른다. */
	void closeActive(String shiftId) {
		jdbc.sql("update plans set status = 'CLOSED' where shift_id = :shiftId and status = 'ACTIVE'")
			.param("shiftId", shiftId)
			.update();
	}

	void activate(String planId, String activatedBy, UUID approvalId) {
		jdbc.sql("""
				update plans set status = 'ACTIVE', activated_at = now(), activated_by = :activatedBy,
				       activated_by_approval = :approvalId
				where plan_id = :planId
				""")
			.param("activatedBy", activatedBy)
			.param("approvalId", approvalId)
			.param("planId", planId)
			.update();
	}

	/** 경로 적용으로 만든 새 버전. 처음부터 ACTIVE다 (적용 승인이 곧 활성화 승인). */
	void insertApplied(String planId, Plan base, int version, String routesJson, String activatedBy, UUID approvalId,
			String candidateId, String truckId) {
		jdbc.sql("""
				insert into plans (plan_id, shift_id, version, status, provisional, routes, unserved, explanation,
				                   activated_at, activated_by, activated_by_approval, applied_candidate_id, applied_truck_id)
				values (:planId, :shiftId, :version, 'ACTIVE', :provisional, cast(:routes as jsonb), cast(:unserved as jsonb),
				        null, now(), :activatedBy, :approvalId, :candidateId, :truckId)
				""")
			.param("planId", planId)
			.param("shiftId", base.shiftId())
			.param("version", version)
			.param("provisional", base.provisional())
			.param("routes", routesJson)
			.param("unserved", base.unservedJson())
			.param("activatedBy", activatedBy)
			.param("approvalId", approvalId)
			.param("candidateId", candidateId)
			.param("truckId", truckId)
			.update();
	}

	private Optional<Plan> one(String sql, String name, String value) {
		return jdbc.sql(sql).param(name, value).query(PlanRepository::map).optional();
	}

	private static Plan map(ResultSet rs, int row) throws SQLException {
		return new Plan(rs.getString("plan_id"), rs.getString("shift_id"), rs.getInt("version"), rs.getString("status"),
				rs.getBoolean("provisional"), rs.getString("routes"), rs.getString("unserved"), rs.getString("explanation"),
				rs.getString("request_id"), rs.getObject("activated_by_approval", UUID.class),
				rs.getString("applied_candidate_id"), rs.getString("applied_truck_id"));
	}
}
