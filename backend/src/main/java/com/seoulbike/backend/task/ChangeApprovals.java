package com.seoulbike.backend.task;

import java.time.Instant;
import java.util.List;
import java.util.Objects;
import java.util.UUID;
import java.util.stream.Collectors;
import java.util.stream.Stream;

import org.springframework.stereotype.Component;

import com.seoulbike.backend.auth.Role;
import com.seoulbike.backend.common.ApiException;
import com.seoulbike.backend.route.RouteChangeGrade;

/**
 * 변경 API 3개(제약 추가·계획 활성화·경로 적용)를 실행하기 전 확인 규칙 (internal-api, FR-18).
 * 에이전트 interrupt와 별개로 backend가 한 번 더 막는다. 실패는 403 APPROVAL_REQUIRED이고, 문장은 모델이 읽고
 * 다시 부를 수 있게 쓴다 (MCP ToolError로 그대로 간다).
 */
@Component
public class ChangeApprovals {

	/** 승인 카드 종류 (계약 ApprovalRequest.kind). */
	public enum Kind {
		SUBMIT_PLAN, ADD_CONSTRAINT, ROUTE_CHANGE
	}

	private final ApprovalRepository approvals;
	private final TaskRepository tasks;

	ChangeApprovals(ApprovalRepository approvals, TaskRepository tasks) {
		this.approvals = approvals;
		this.tasks = tasks;
	}

	/**
	 * 계획·제약: 이 작업의 그 종류 카드를 관리자가 승인했어야 하고, 바꾸려는 근무조가 그 작업의 근무조여야 한다.
	 * 근무조를 보지 않으면 PM 작업의 승인으로 AM 근무조를 바꿀 수 있다.
	 *
	 * @return 승인한 관리자의 사용자 id (감사 기록의 행위자)
	 */
	public String requireManagerApproval(UUID taskId, UUID approvalId, Kind kind, String shiftId) {
		return managerApproval(taskId, approvalId, kind, shiftId).decidedBy();
	}

	/**
	 * 계획 활성화: SUBMIT_PLAN 관리자 승인(requireManagerApproval과 같은 규칙)이 이 계획에 대한 것이어야 한다.
	 * 카드의 planId와 candidateId(후보 id = planId) 중 적힌 값이 모두 planId와 같아야 한다. 둘 다 비어 있으면 거부한다.
	 * 어느 계획인지 적히지 않은 승인으로 아무 계획이나 활성화하게 두면, 관리자가 본 적 없는 계획이 ACTIVE가 된다.
	 *
	 * @return 승인한 관리자의 사용자 id
	 */
	public String requirePlanApproval(UUID taskId, UUID approvalId, String planId, String shiftId) {
		var approval = managerApproval(taskId, approvalId, Kind.SUBMIT_PLAN, shiftId);
		List<String> approvedPlans = Stream.of(approval.planId(), approval.candidateId())
			.filter(Objects::nonNull)
			.distinct()
			.toList();
		if (approvedPlans.isEmpty()) {
			throw denied("approvalId " + approvalId + " 카드에 활성화할 계획(planId)이 없다. 계획 " + planId
					+ "를 적은 SUBMIT_PLAN 카드로 승인을 받은 뒤 다시 호출한다.");
		}
		if (!approvedPlans.equals(List.of(planId))) {
			throw denied("approvalId " + approvalId + "는 계획 " + String.join(", ", approvedPlans) + "의 승인이다. 계획 "
					+ planId + "를 활성화하려면 그 계획의 승인을 받는다.");
		}
		return approval.decidedBy();
	}

	private ApprovalRepository.Approval managerApproval(UUID taskId, UUID approvalId, Kind kind, String shiftId) {
		var approval = approvals.find(approvalId, taskId)
			.orElseThrow(() -> denied("작업 " + taskId + "에 approvalId " + approvalId + "가 없다. 승인 카드를 받은 뒤 그 id로 다시 호출한다."));
		if (!kind.name().equals(approval.kind()) || approval.approverRole() != Role.MANAGER) {
			throw denied("approvalId " + approvalId + "는 " + approval.kind() + " 카드다. " + kind + " 관리자 승인이 필요하다.");
		}
		if (approval.status() != ApprovalRepository.Status.APPROVED) {
			throw denied("approvalId " + approvalId + "는 " + approval.status() + " 상태다. 승인 후 다시 호출한다.");
		}
		String taskShift = tasks.find(taskId).map(Task::shiftId).orElse(null);
		if (!shiftId.equals(taskShift)) {
			throw denied("approvalId " + approvalId + "는 근무조 " + taskShift + " 작업의 승인이다. 근무조 " + shiftId
					+ "를 바꾸려면 그 근무조 작업에서 승인을 받는다.");
		}
		return approval;
	}

	/**
	 * 경로 변경: 이 작업에서 이 후보에 필요한 승인이 순서대로 모두 APPROVED여야 한다. 기사 승인은 그 트럭 기사의 것이어야 한다.
	 * 등급은 backend가 계산한 값을 받는다. 승인 카드에 적힌 등급은 에이전트가 보낸 값이라 믿지 않는다.
	 *
	 * @param approvalId 이 후보의 마지막 카드(기사 수락)
	 * @return 마지막으로 승인한 사람(그 트럭 기사)의 사용자 id
	 */
	public String requireRouteChange(UUID taskId, UUID approvalId, String truckId, String candidateId,
			RouteChangeGrade grade) {
		List<Role> required = grade.requiredApprovers();
		if (required.isEmpty()) {
			throw ApiException.conflict("NOTHING_TO_APPLY", "변경 등급이 NONE이라 적용할 것이 없다");
		}
		List<ApprovalRepository.Approval> approved = approvals.approvedRouteChanges(taskId, candidateId);
		String order = required.stream().map(Role::name).collect(Collectors.joining(" → "));

		Instant previous = Instant.MIN;
		ApprovalRepository.Approval last = null;
		for (Role role : required) {
			Instant notBefore = previous;
			last = approved.stream()
				.filter(a -> a.approverRole() == role && !a.decidedAt().isBefore(notBefore))
				.filter(a -> role != Role.DRIVER || truckId.equals(a.truckId()))
				.findFirst()
				.orElseThrow(() -> denied("후보 " + candidateId + "에 " + (role == Role.DRIVER ? truckId + " 기사" : "관리자")
						+ " 승인이 없다. 필요한 순서: " + order + ". 앞의 승인을 받은 뒤 다시 호출한다."));
			previous = last.decidedAt();
		}
		if (!last.approvalId().equals(approvalId)) {
			throw denied("approvalId " + approvalId + "는 후보 " + candidateId + "의 마지막 승인이 아니다. 마지막 승인은 "
					+ last.approvalId() + "다.");
		}
		return last.decidedBy();
	}

	private static ApiException denied(String detail) {
		return ApiException.forbidden("APPROVAL_REQUIRED", detail);
	}
}
