package com.seoulbike.backend.plan;

import java.sql.ResultSet;
import java.sql.SQLException;
import java.time.LocalDate;
import java.util.Collection;
import java.util.Map;
import java.util.Optional;
import java.util.UUID;
import java.util.stream.Collectors;

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

	/** 대여소 ID → 이름 (기준 정보, 운영 여부와 상관없이). 계획 JSON에는 ID만 있어 화면 응답에 이름을 붙일 때 쓴다. */
	Map<String, String> stationNames(Collection<String> stationIds) {
		if (stationIds.isEmpty()) {
			return Map.of();
		}
		return jdbc.sql("select station_id, name from ref.stations where station_id in (:ids)")
			.param("ids", stationIds)
			.query((rs, i) -> Map.entry(rs.getString("station_id"), rs.getString("name")))
			.list()
			.stream()
			.collect(Collectors.toMap(Map.Entry::getKey, Map.Entry::getValue));
	}

	/** 기사 경로와 그 경로가 속한 근무조. 기사 계정에는 근무조가 없어 화면이 현장 보고·구역 대여소 조회에 이 근무조를 쓴다. */
	record DriverRoute(String shiftId, String routeJson) {
	}

	/**
	 * 그날(shiftId `{regionId}:{yyyy-MM-dd}:{slot}`의 날짜) 근무조의 ACTIVE 계획 중 이 트럭이 있고 가장 늦게 활성화된 것의 그 트럭
	 * 경로(계약 Route JSON). 기사 계정에는 근무조가 없어 트럭과 날짜로 찾는다. 지난 근무조의 ACTIVE는 닫히지 않고 남으므로
	 * 날짜로 거르지 않으면 어제 경로가 나온다.
	 */
	Optional<DriverRoute> activeRouteOf(String truckId, LocalDate day) {
		return jdbc.sql("""
				select p.shift_id, r.route::text as route
				from plans p cross join lateral jsonb_array_elements(p.routes) as r(route)
				where p.status = 'ACTIVE' and split_part(p.shift_id, ':', 2) = :day and r.route ->> 'truckId' = :truckId
				order by p.activated_at desc nulls last, p.created_at desc
				limit 1
				""")
			.param("day", day.toString())
			.param("truckId", truckId)
			.query((rs, i) -> new DriverRoute(rs.getString("shift_id"), rs.getString("route")))
			.optional();
	}

	/** 같은 버전 안에서 경로 JSON만 고친다 (정류장 진행 기록, RouteProgress). 경로를 바꾸는 변경은 새 버전으로 남긴다. */
	void updateRoutes(String planId, String routesJson) {
		jdbc.sql("update plans set routes = cast(:routes as jsonb) where plan_id = :planId")
			.param("routes", routesJson)
			.param("planId", planId)
			.update();
	}

	/** 근무조에서 활성화된 적 있는 계획 버전 수. 활성화한 승인이 APPROVED가 아니면(없으면) 무승인이다. */
	record Activations(int approved, int unapproved) {
	}

	Activations activations(String shiftId) {
		return jdbc.sql("""
				select count(*) filter (where a.status = 'APPROVED') as approved,
				       count(*) filter (where a.status is distinct from 'APPROVED') as unapproved
				from plans p left join approvals a on a.approval_id = p.activated_by_approval
				where p.shift_id = :shiftId and p.activated_at is not null
				""")
			.param("shiftId", shiftId)
			.query((rs, i) -> new Activations(rs.getInt("approved"), rs.getInt("unapproved")))
			.single();
	}

	/**
	 * 후보를 만든 계산 요청.
	 *
	 * @param planVersion 요청이 기준으로 삼은 활성 version (요청 당시 활성 계획이 없었으면 0)
	 * @param taskId 요청한 작업. 없으면 null
	 */
	record SolveBasis(int planVersion, UUID taskId) {
	}

	Optional<SolveBasis> solveBasis(String requestId) {
		return jdbc.sql("select plan_version, task_id from solve_requests where request_id = :requestId")
			.param("requestId", requestId)
			.query((rs, i) -> new SolveBasis(rs.getInt("plan_version"), rs.getObject("task_id", UUID.class)))
			.optional();
	}

	int nextVersion(String shiftId) {
		return jdbc.sql("select coalesce(max(version), 0) + 1 from plans where shift_id = :shiftId")
			.param("shiftId", shiftId)
			.query(Integer.class)
			.single();
	}

	/** @param provisional optimizer가 답하지 못해 backend가 만든 임시안 (계약 Plan.provisional) */
	void insertCandidate(String planId, String shiftId, int version, boolean provisional, String routesJson,
			String unservedJson, String explanation, String requestId) {
		jdbc.sql("""
				insert into plans (plan_id, shift_id, version, status, provisional, routes, unserved, explanation, request_id)
				values (:planId, :shiftId, :version, 'PROPOSED', :provisional, cast(:routes as jsonb), cast(:unserved as jsonb),
				        :explanation, :requestId)
				""")
			.param("planId", planId)
			.param("shiftId", shiftId)
			.param("version", version)
			.param("provisional", provisional)
			.param("routes", routesJson)
			.param("unserved", unservedJson)
			.param("explanation", explanation)
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
