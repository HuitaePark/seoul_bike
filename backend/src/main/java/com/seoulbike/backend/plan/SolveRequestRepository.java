package com.seoulbike.backend.plan;

import java.util.Optional;
import java.util.UUID;

import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Repository;

@Repository
class SolveRequestRepository {

	/** 잠근 요청 행. result_event_id가 있으면 이미 결과를 받은 요청이다. */
	record Locked(String shiftId, boolean hasResult) {
	}

	private final JdbcClient jdbc;

	SolveRequestRepository(JdbcClient jdbc) {
		this.jdbc = jdbc;
	}

	/** 근무조·계획 버전마다 1부터. 부르는 쪽이 근무조 잠금(PlanRepository.lockShift)을 잡고 있어야 겹치지 않는다. */
	int nextSeq(String shiftId, int planVersion) {
		return jdbc.sql("""
				select coalesce(max(seq), 0) + 1 from solve_requests where shift_id = :shiftId and plan_version = :planVersion
				""")
			.param("shiftId", shiftId)
			.param("planVersion", planVersion)
			.query(Integer.class)
			.single();
	}

	void insert(String requestId, String shiftId, int planVersion, int seq, SolveGateway.Kind kind, UUID taskId,
			String requestJson) {
		jdbc.sql("""
				insert into solve_requests (request_id, shift_id, plan_version, seq, kind, task_id, request)
				values (:requestId, :shiftId, :planVersion, :seq, :kind, :taskId, cast(:request as jsonb))
				""")
			.param("requestId", requestId)
			.param("shiftId", shiftId)
			.param("planVersion", planVersion)
			.param("seq", seq)
			.param("kind", kind.name())
			.param("taskId", taskId)
			.param("request", requestJson)
			.update();
	}

	/**
	 * 브로커 확인을 받지 못한 요청. 실제로는 전달됐을 수 있어 행과 seq는 남긴다 (같은 id를 다른 내용으로 다시 쓰면 optimizer가 멈춘다).
	 * result는 비워 두므로 늦게 온 결과는 그대로 저장된다.
	 */
	void markUnsent(String requestId) {
		jdbc.sql("""
				update solve_requests set status = 'FAILED', completed_at = now()
				where request_id = :requestId and result_event_id is null
				""")
			.param("requestId", requestId)
			.update();
	}

	/**
	 * 요청 행을 잠근다 (트랜잭션 안에서). 본 컨테이너와 재시도(solve.results.retry) 컨테이너가 같은 요청의 결과를
	 * 함께 저장하지 않게 한 줄로 세운다. 요청이 없으면 비어 있다.
	 */
	Optional<Locked> lock(String requestId) {
		return jdbc.sql("select shift_id, result_event_id is not null as has_result from solve_requests where request_id = :requestId for update")
			.param("requestId", requestId)
			.query((rs, i) -> new Locked(rs.getString("shift_id"), rs.getBoolean("has_result")))
			.optional();
	}

	void complete(String requestId, String status, String resultJson, UUID resultEventId) {
		jdbc.sql("""
				update solve_requests
				set status = :status, result = cast(:result as jsonb), result_event_id = :eventId, completed_at = now()
				where request_id = :requestId
				""")
			.param("status", status)
			.param("result", resultJson)
			.param("eventId", resultEventId)
			.param("requestId", requestId)
			.update();
	}

	/** 현장 보고에서 시작한 작업인가. 변경 등급의 출발점(RouteDiff.Origin)을 정한다. */
	boolean fromFieldReport(UUID taskId) {
		return jdbc.sql("select type = 'FIELD_REPORT' from ai_tasks where task_id = :taskId")
			.param("taskId", taskId)
			.query(Boolean.class)
			.optional()
			.orElse(false);
	}
}
