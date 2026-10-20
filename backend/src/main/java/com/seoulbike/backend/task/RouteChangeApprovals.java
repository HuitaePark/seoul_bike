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
 * 경로 변경을 적용하기 전 확인 규칙 (internal-api /internal/routes/{truckId}/apply, FR-18).
 * 이 작업에서 이 후보에 필요한 승인이 순서대로 모두 APPROVED여야 한다. 기사 승인은 그 트럭 기사의 것이어야 한다.
 * 등급은 backend가 계산한 값을 받는다. 승인 카드에 적힌 등급은 에이전트가 보낸 값이라 믿지 않는다.
 */
@Component
public class RouteChangeApprovals {

	private final ApprovalRepository approvals;

	RouteChangeApprovals(ApprovalRepository approvals) {
		this.approvals = approvals;
	}

	/** @param approvalId 이 후보의 마지막 카드(기사 수락) */
	public void requireApproved(UUID taskId, UUID approvalId, String truckId, String candidateId, RouteChangeGrade grade) {
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
				.orElseThrow(() -> ApiException.forbidden("APPROVAL_REQUIRED", "후보 " + candidateId + "에 "
						+ (role == Role.DRIVER ? truckId + " 기사" : "관리자") + " 승인이 없다. 필요한 순서: " + order
						+ ". 앞의 승인을 받은 뒤 다시 호출한다."));
			previous = last.decidedAt();
		}
		if (!last.approvalId().equals(approvalId)) {
			throw ApiException.forbidden("APPROVAL_REQUIRED",
					"approvalId " + approvalId + "는 후보 " + candidateId + "의 마지막 승인이 아니다. 마지막 승인은 " + last.approvalId() + "다.");
		}
	}
}
