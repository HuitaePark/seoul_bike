package com.seoulbike.backend.task;

import java.util.Set;
import java.util.UUID;

import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.transaction.support.TransactionTemplate;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ObjectNode;

import com.seoulbike.backend.audit.AuditLog;
import com.seoulbike.backend.auth.CurrentUser;
import com.seoulbike.backend.auth.Role;
import com.seoulbike.backend.common.ApiException;

@Service
public class TaskService {

	record CreateTask(TaskType type, String shiftId, String text) {
	}

	record TaskAccepted(UUID taskId, TaskStatus status, String streamUrl) {
	}

	private final TaskRepository tasks;
	private final ApprovalRepository approvals;
	private final TaskCommandPublisher publisher;
	private final JsonMapper json;
	private final TransactionTemplate tx;
	private final AuditLog audit;

	TaskService(TaskRepository tasks, ApprovalRepository approvals, TaskCommandPublisher publisher, JsonMapper json,
			TransactionTemplate tx, AuditLog audit) {
		this.tasks = tasks;
		this.approvals = approvals;
		this.publisher = publisher;
		this.json = json;
		this.tx = tx;
		this.audit = audit;
	}

	TaskAccepted create(CurrentUser user, CreateTask req) {
		// 현장 보고는 보고 기록과 함께 /api/reports가 만든다. 여기로 오면 reportId 없는 명령이 된다 (G13).
		if (req.type() == TaskType.FIELD_REPORT) {
			throw new ApiException(HttpStatus.BAD_REQUEST, "USE_REPORTS_API", "현장 보고 작업은 POST /api/reports로 만든다");
		}
		if (!allowedTypes(user.role()).contains(req.type())) {
			throw ApiException.forbidden("TASK_TYPE_NOT_ALLOWED", user.role() + " 역할은 " + req.type() + " 작업을 만들 수 없다");
		}
		if ((req.type() == TaskType.REQUEST || req.type() == TaskType.QUESTION)
				&& (req.text() == null || req.text().isBlank())) {
			throw new ApiException(HttpStatus.BAD_REQUEST, "TEXT_REQUIRED", req.type() + " 작업은 text가 필요하다");
		}
		return start(user, req.type(), req.shiftId(), req.text(), null);
	}

	/**
	 * 현장 보고(POST /api/reports)가 재경로 작업을 만든다. 보고 값(계약 FieldReport)은 명령에 실어
	 * agent가 다시 읽지 않게 한다. 큐에 못 넣으면 503 ApiException이고 작업은 남지 않는다.
	 */
	public UUID startFieldReport(CurrentUser driver, String shiftId, ObjectNode report) {
		return start(driver, TaskType.FIELD_REPORT, shiftId, null, report).taskId();
	}

	/**
	 * 작업을 먼저 커밋하고 큐에 넣는다. 반대로 하면 에이전트 이벤트가 커밋보다 먼저 와서 "모르는 작업"으로 버려진다.
	 * 큐에 못 넣으면 작업을 지우고 503을 돌려준다.
	 */
	private TaskAccepted start(CurrentUser user, TaskType type, String shiftId, String text, ObjectNode report) {
		UUID taskId = UUID.randomUUID();
		String traceId = UUID.randomUUID().toString().replace("-", "");
		tasks.insert(taskId, type, shiftId, text, user.id(), traceId);
		try {
			publisher.newTask(taskId, type, shiftId, text, traceId, user, report);
		}
		catch (ApiException e) {
			tasks.delete(taskId);
			throw e;
		}
		return new TaskAccepted(taskId, TaskStatus.QUEUED, "/api/ai/tasks/" + taskId + "/stream");
	}

	Task get(CurrentUser user, UUID taskId) {
		return tasks.findVisible(taskId, user)
			.orElseThrow(() -> ApiException.notFound("TASK_NOT_FOUND", "작업 " + taskId + "가 없거나 볼 권한이 없다"));
	}

	TaskRepository.Page list(CurrentUser user, boolean mine, TaskStatus status, String shiftId, int page, int size) {
		return tasks.list(user, mine, status, shiftId, page, size);
	}

	/**
	 * 승인 주체만 결정할 수 있다: 계획·제약은 관리자, 경로 변경은 해당 트럭 기사.
	 * 결정을 커밋한 뒤 같은 thread_id로 에이전트를 재개한다 (UR-18). 커밋 전에 보내면 재개한 에이전트가
	 * 변경 API를 부를 때 아직 승인이 안 보여 403을 받는다. 재개를 못 보내면 결정을 되돌리고 503을 돌려준다.
	 */
	void decide(CurrentUser user, UUID taskId, ApprovalDecision decision) {
		Task task = tx.execute(s -> record(user, taskId, decision));
		try {
			publisher.resume(taskId, task.shiftId(), task.traceId(), decision.approvalId(), decision, user);
		}
		catch (ApiException e) {
			tx.executeWithoutResult(s -> {
				approvals.reopen(decision.approvalId());
				tasks.waitForApproval(taskId, decision.approvalId());
				audit.record(task.shiftId(), AuditLog.Type.APPROVAL_REOPENED, AuditLog.Actor.system(),
						decision.approvalId().toString(), taskId, "에이전트에 재개 명령을 보내지 못해 결정을 되돌렸다. 다시 결정할 수 있다");
			});
			throw e;
		}
	}

	private Task record(CurrentUser user, UUID taskId, ApprovalDecision decision) {
		Task task = get(user, taskId);
		var approval = approvals.find(decision.approvalId(), taskId)
			.orElseThrow(() -> ApiException.notFound("APPROVAL_NOT_FOUND", "이 작업에 승인 요청 " + decision.approvalId() + "가 없다"));
		switch (approval.status()) {
			case PENDING -> {
			}
			case SUPERSEDED -> throw ApiException.conflict("APPROVAL_SUPERSEDED", "더 새로운 승인 요청으로 대체됐다");
			default -> throw ApiException.conflict("APPROVAL_ALREADY_DECIDED", "이미 처리된 승인 요청이다");
		}
		if (!approval.canBeDecidedBy(user)) {
			throw ApiException.forbidden("NOT_APPROVER", "이 승인 요청의 승인 주체가 아니다");
		}
		var status = decision.decision() == ApprovalDecision.Decision.APPROVE ? ApprovalRepository.Status.APPROVED
				: ApprovalRepository.Status.REJECTED;
		if (!approvals.decide(approval.approvalId(), status, json.writeValueAsString(decision), user.id())) {
			throw ApiException.conflict("APPROVAL_ALREADY_DECIDED", "이미 처리된 승인 요청이다");
		}
		// 작업이 이미 끝났거나 다른 요청을 기다리면 재개하지 않는다. 예외로 위의 결정 기록도 롤백된다.
		if (!tasks.resume(taskId, approval.approvalId())) {
			throw ApiException.conflict("APPROVAL_SUPERSEDED", "작업이 이 승인 요청을 더 기다리지 않는다");
		}
		boolean approved = status == ApprovalRepository.Status.APPROVED;
		int excluded = decision.excludeStationIds() == null ? 0 : decision.excludeStationIds().size();
		audit.record(task.shiftId(), approved ? AuditLog.Type.APPROVAL_APPROVED : AuditLog.Type.APPROVAL_REJECTED,
				AuditLog.Actor.of(user), approval.approvalId().toString(), taskId,
				approval.kind() + " 카드를 " + (approved ? "승인" : "거절") + "했다"
						+ (excluded > 0 ? " (뺄 대여소 " + excluded + "곳)" : ""));
		return task;
	}

	private static Set<TaskType> allowedTypes(Role role) {
		return switch (role) {
			case MANAGER -> Set.of(TaskType.PLAN_DRAFT, TaskType.REQUEST, TaskType.QUESTION);
			case DRIVER, EXECUTIVE -> Set.of(TaskType.QUESTION);
		};
	}
}
