package com.seoulbike.backend.task;

import java.sql.ResultSet;
import java.sql.SQLException;
import java.time.Instant;
import java.time.OffsetDateTime;
import java.util.List;
import java.util.Optional;
import java.util.UUID;

import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Repository;

import com.seoulbike.backend.auth.CurrentUser;
import com.seoulbike.backend.auth.Role;

@Repository
class ApprovalRepository {

	enum Status {
		PENDING, APPROVED, REJECTED, SUPERSEDED
	}

	record Approval(UUID approvalId, UUID taskId, String kind, Role approverRole, String truckId, String candidateId,
			Status status, Instant decidedAt, String decidedBy) {

		/** 계획·제약은 관리자, 경로 변경은 그 트럭의 기사만 결정한다. */
		boolean canBeDecidedBy(CurrentUser user) {
			return switch (approverRole) {
				case MANAGER -> user.is(Role.MANAGER);
				case DRIVER -> user.is(Role.DRIVER) && truckId != null && truckId.equals(user.truckId());
				case EXECUTIVE -> false;
			};
		}
	}

	record NewApproval(UUID approvalId, UUID taskId, String kind, String approverRole, String truckId,
			String candidateId, String grade, String requestJson) {
	}

	private static final String COLUMNS = "approval_id, task_id, kind, approver_role, truck_id, candidate_id, status, decided_at, decided_by";

	private final JdbcClient jdbc;

	ApprovalRepository(JdbcClient jdbc) {
		this.jdbc = jdbc;
	}

	/** 같은 작업에 새 승인 요청이 오거나 작업이 끝나면 대기 중인 요청은 낡은 것이다 (계약: 409 superseded). */
	void supersedePending(UUID taskId) {
		jdbc.sql("update approvals set status = 'SUPERSEDED' where task_id = :taskId and status = 'PENDING'")
			.param("taskId", taskId)
			.update();
	}

	void insertPending(NewApproval a) {
		jdbc.sql("""
				insert into approvals (approval_id, task_id, kind, approver_role, truck_id, candidate_id, grade, status, request)
				values (:approvalId, :taskId, :kind, :approverRole, :truckId, :candidateId, :grade, 'PENDING',
				        cast(:request as jsonb))
				on conflict (approval_id) do nothing
				""")
			.param("approvalId", a.approvalId())
			.param("taskId", a.taskId())
			.param("kind", a.kind())
			.param("approverRole", a.approverRole())
			.param("truckId", a.truckId())
			.param("candidateId", a.candidateId())
			.param("grade", a.grade())
			.param("request", a.requestJson())
			.update();
	}

	Optional<Approval> find(UUID approvalId, UUID taskId) {
		return jdbc.sql("select " + COLUMNS + " from approvals where approval_id = :approvalId and task_id = :taskId")
			.param("approvalId", approvalId)
			.param("taskId", taskId)
			.query(ApprovalRepository::map)
			.optional();
	}

	/** 이 작업에서 이 후보 경로에 대해 승인된 카드들. 결정한 순서대로. */
	List<Approval> approvedRouteChanges(UUID taskId, String candidateId) {
		return jdbc.sql("select " + COLUMNS + """
				 from approvals
				 where task_id = :taskId and kind = 'ROUTE_CHANGE' and candidate_id = :candidateId and status = 'APPROVED'
				 order by decided_at
				""")
			.param("taskId", taskId)
			.param("candidateId", candidateId)
			.query(ApprovalRepository::map)
			.list();
	}

	/** PENDING일 때만 바꾼다. 동시에 두 번 눌러도 한 번만 반영된다. */
	boolean decide(UUID approvalId, Status status, String decisionJson, String decidedBy) {
		return jdbc.sql("""
				update approvals set status = :status, decision = cast(:decision as jsonb),
				       decided_by = :decidedBy, decided_at = now()
				where approval_id = :approvalId and status = 'PENDING'
				""")
			.param("status", status.name())
			.param("decision", decisionJson)
			.param("decidedBy", decidedBy)
			.param("approvalId", approvalId)
			.update() == 1;
	}

	/** 결정을 에이전트에 전하지 못했을 때 되돌린다. 다시 결정할 수 있게 된다. */
	void reopen(UUID approvalId) {
		jdbc.sql("""
				update approvals set status = 'PENDING', decision = null, decided_by = null, decided_at = null
				where approval_id = :approvalId
				""")
			.param("approvalId", approvalId)
			.update();
	}

	private static Approval map(ResultSet rs, int row) throws SQLException {
		OffsetDateTime decidedAt = rs.getObject("decided_at", OffsetDateTime.class);
		return new Approval(rs.getObject("approval_id", UUID.class), rs.getObject("task_id", UUID.class),
				rs.getString("kind"), Role.valueOf(rs.getString("approver_role")), rs.getString("truck_id"),
				rs.getString("candidate_id"), Status.valueOf(rs.getString("status")),
				decidedAt == null ? null : decidedAt.toInstant(), rs.getString("decided_by"));
	}
}
