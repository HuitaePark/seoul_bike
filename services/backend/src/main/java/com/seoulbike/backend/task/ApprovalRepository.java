package com.seoulbike.backend.task;

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

	record Approval(UUID approvalId, UUID taskId, String kind, Role approverRole, String truckId, Status status) {

		/** 계획·제약은 관리자, 경로 변경은 그 트럭의 기사만 결정한다. */
		boolean canBeDecidedBy(CurrentUser user) {
			return switch (approverRole) {
				case MANAGER -> user.is(Role.MANAGER);
				case DRIVER -> user.is(Role.DRIVER) && truckId != null && truckId.equals(user.truckId());
				case EXECUTIVE -> false;
			};
		}
	}

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

	void insertPending(UUID approvalId, UUID taskId, String kind, String approverRole, String truckId,
			String requestJson) {
		jdbc.sql("""
				insert into approvals (approval_id, task_id, kind, approver_role, truck_id, status, request)
				values (:approvalId, :taskId, :kind, :approverRole, :truckId, 'PENDING', cast(:request as jsonb))
				on conflict (approval_id) do nothing
				""")
			.param("approvalId", approvalId)
			.param("taskId", taskId)
			.param("kind", kind)
			.param("approverRole", approverRole)
			.param("truckId", truckId)
			.param("request", requestJson)
			.update();
	}

	Optional<Approval> find(UUID approvalId, UUID taskId) {
		return jdbc.sql("""
				select approval_id, task_id, kind, approver_role, truck_id, status
				from approvals where approval_id = :approvalId and task_id = :taskId
				""")
			.param("approvalId", approvalId)
			.param("taskId", taskId)
			.query((rs, i) -> new Approval(rs.getObject("approval_id", UUID.class), rs.getObject("task_id", UUID.class),
					rs.getString("kind"), Role.valueOf(rs.getString("approver_role")), rs.getString("truck_id"),
					Status.valueOf(rs.getString("status"))))
			.optional();
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
}
