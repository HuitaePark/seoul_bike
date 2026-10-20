package com.seoulbike.backend.task;

import java.time.Instant;
import java.util.List;
import java.util.UUID;
import java.util.stream.Collectors;

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

	ChangeApprovals(ApprovalRepository approvals) {
		this.approvals = approvals;
	}

	/** 계획·제약: 이 작업의 그 종류 카드를 관리자가 승인했어야 한다. */
	public void requireManagerApproval(UUID taskId, UUID approvalId, Kind kind) {
		var approval = approvals.find(approvalId, taskId)
			.orElseThrow(() -> denied("작업 " + taskId + "에 approvalId " + approvalId + "가 없다. 승인 카드를 받은 뒤 그 id로 다시 호출한다."));
		if (!kind.name().equals(approval.kind()) || approval.approverRole() != Role.MANAGER) {
			throw denied("approvalId " + approvalId + "는 " + approval.kind() + " 카드다. " + kind + " 관리자 승인이 필요하다.");
		}
		if (approval.status() != ApprovalRepository.Status.APPROVED) {
			throw denied("approvalId " + approvalId + "는 " + approval.status() + " 상태다. 승인 후 다시 호출한다.");
		}
	}

	/**
	 * 경로 변경: 이 작업에서 이 후보에 필요한 승인이 순서대로 모두 APPROVED여야 한다. 기사 승인은 그 트럭 기사의 것이어야 한다.
	 * 등급은 backend가 계산한 값을 받는다. 승인 카드에 적힌 등급은 에이전트가 보낸 값이라 믿지 않는다.
	 *
	 * @param approvalId 이 후보의 마지막 카드(기사 수락)
	 */
	public void requireRouteChange(UUID taskId, UUID approvalId, String truckId, String candidateId,
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
	}

	private static ApiException denied(String detail) {
		return ApiException.forbidden("APPROVAL_REQUIRED", detail);
	}
}
