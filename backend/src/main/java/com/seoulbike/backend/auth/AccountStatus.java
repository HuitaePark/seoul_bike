package com.seoulbike.backend.auth;

/** PENDING 가입 신청 · ACTIVE 사용 중 · DISABLED 비활성. ACTIVE만 로그인할 수 있다. */
public enum AccountStatus {
	PENDING, ACTIVE, DISABLED
}
