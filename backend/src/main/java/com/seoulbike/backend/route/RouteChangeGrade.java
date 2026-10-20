package com.seoulbike.backend.route;

import java.util.List;

import com.seoulbike.backend.auth.Role;

/**
 * 변경 등급과 승인 순서 (PRD 6.6, 계약 PlanCandidate.requiredApprovers).
 * 등급 → 승인자 대응은 여기 한 곳에만 둔다. 에이전트·MCP는 이 값을 받아 그대로 카드를 보낸다.
 */
public enum RouteChangeGrade {

	/** 정류장 순서·대수가 그대로다. 적용할 것이 없다. */
	NONE(List.of()),
	/** 그 트럭 기사가 수락한다. */
	MINOR(List.of(Role.DRIVER)),
	/** 관리자가 승인한 뒤 기사가 수락한다. */
	MAJOR(List.of(Role.MANAGER, Role.DRIVER));

	private final List<Role> requiredApprovers;

	RouteChangeGrade(List<Role> requiredApprovers) {
		this.requiredApprovers = requiredApprovers;
	}

	public List<Role> requiredApprovers() {
		return requiredApprovers;
	}
}
