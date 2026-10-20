package com.seoulbike.backend.constraint;

import java.util.Objects;
import java.util.Set;
import java.util.UUID;

import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;

import com.seoulbike.backend.common.ApiException;
import com.seoulbike.backend.constraint.ConstraintController.Constraint;
import com.seoulbike.backend.constraint.ConstraintController.ConstraintRequest;
import com.seoulbike.backend.constraint.ConstraintController.ConstraintType;
import com.seoulbike.backend.task.ChangeApprovals;

/**
 * 관리자가 승인한 제약만 넣는다 (FR-09, FR-18). 같은 승인으로 다시 부르면 처음 만든 제약을 돌려준다 (agent 재시도).
 * 대여소가 있는지는 대여소 데이터가 들어오면(M3, PR #10 시드) 확인한다.
 */
@Service
class ConstraintService {

	/** 값이 있어야 뜻이 있는 종류: TARGET_ADJUST는 가감 대수, INVENTORY_OVERRIDE는 실제 재고 (계약 value). */
	private static final Set<ConstraintType> NEEDS_VALUE = Set.of(ConstraintType.TARGET_ADJUST,
			ConstraintType.INVENTORY_OVERRIDE);

	private final ConstraintRepository constraints;
	private final ChangeApprovals approvals;

	ConstraintService(ConstraintRepository constraints, ChangeApprovals approvals) {
		this.constraints = constraints;
		this.approvals = approvals;
	}

	Constraint add(ConstraintRequest req) {
		if (!req.validTo().isAfter(req.validFrom())) {
			throw invalid("validTo는 validFrom보다 뒤여야 한다");
		}
		if (NEEDS_VALUE.contains(req.type()) && req.value() == null) {
			throw invalid(req.type() + " 제약은 value가 필요하다 (TARGET_ADJUST 가감 대수, INVENTORY_OVERRIDE 실제 재고)");
		}
		if (req.type() == ConstraintType.INVENTORY_OVERRIDE && req.value() < 0) {
			throw invalid("INVENTORY_OVERRIDE의 value(실제 재고)는 0 이상이어야 한다");
		}
		approvals.requireManagerApproval(req.taskId(), req.approvalId(), ChangeApprovals.Kind.ADD_CONSTRAINT, req.shiftId());
		if (constraints.insertIfAbsent(UUID.randomUUID(), req)) {
			return constraints.findByApproval(req.approvalId());
		}
		// 재시도: 같은 내용이면 처음 만든 제약, 다른 내용이면 승인을 다른 변경에 쓰려는 것이다.
		Constraint existing = constraints.findByApproval(req.approvalId());
		if (!sameContent(existing, req)) {
			throw ApiException.conflict("APPROVAL_ALREADY_USED",
					"approvalId " + req.approvalId() + "로 이미 다른 제약(" + existing.constraintId() + ")을 넣었다. 새 제약은 새 승인을 받는다.");
		}
		return existing;
	}

	private static boolean sameContent(Constraint c, ConstraintRequest r) {
		return c.shiftId().equals(r.shiftId()) && c.type() == r.type() && c.stationIds().equals(r.stationIds())
				&& Objects.equals(c.value(), r.value()) && c.validFrom().isEqual(r.validFrom())
				&& c.validTo().isEqual(r.validTo()) && c.reason().equals(r.reason());
	}

	private static ApiException invalid(String detail) {
		return new ApiException(HttpStatus.BAD_REQUEST, "INVALID_REQUEST", detail);
	}
}
