package com.seoulbike.backend.auth;

import org.springframework.security.oauth2.jwt.Jwt;

/** 액세스 토큰에서 꺼낸 로그인 사용자. 계약의 User 스키마와 같은 모양이다. */
public record CurrentUser(String id, String name, Role role, String truckId) {

	static final String CLAIM_NAME = "name";
	static final String CLAIM_ROLE = "role";
	static final String CLAIM_TRUCK = "truckId";
	/** 계정의 토큰 버전. 비활성화하면 DB 쪽이 올라가 그 전에 받은 토큰이 모두 무효가 된다. */
	static final String CLAIM_TOKEN_VERSION = "tv";

	public static CurrentUser from(Jwt jwt) {
		return new CurrentUser(jwt.getSubject(), jwt.getClaimAsString(CLAIM_NAME),
				Role.valueOf(jwt.getClaimAsString(CLAIM_ROLE)), jwt.getClaimAsString(CLAIM_TRUCK));
	}

	public boolean is(Role other) {
		return role == other;
	}
}
