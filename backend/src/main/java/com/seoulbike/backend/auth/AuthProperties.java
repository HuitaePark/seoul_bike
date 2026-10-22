package com.seoulbike.backend.auth;

import java.nio.charset.StandardCharsets;
import java.time.Duration;

import org.springframework.boot.context.properties.ConfigurationProperties;

/**
 * @param jwtSecret          HS256 서명 키. 32바이트 미만이면 기동을 막는다 (서버 .env의 JWT_SECRET)
 * @param tokenTtl           액세스 토큰 유효 시간
 * @param seedPassword       비어 있지 않으면 기동할 때 시드 계정(관리자·기사·책임자)을 만든다
 * @param loginMaxFailures   같은 아이디로 이만큼 틀리면 {@code loginFailureWindow}가 끝날 때까지 로그인을 막는다
 * @param loginFailureWindow 첫 실패부터 세는 창
 * @param maxPendingSignups  승인 대기 가입 신청이 이만큼 쌓이면 새 신청을 받지 않는다
 * @param guestEnabled       로그인 없이 들어오기(POST /api/auth/guest, 관리자 권한 손님 계정)를 연다
 */
@ConfigurationProperties("app.auth")
record AuthProperties(String jwtSecret, Duration tokenTtl, String seedPassword, int loginMaxFailures,
		Duration loginFailureWindow, int maxPendingSignups, boolean guestEnabled) {

	AuthProperties {
		if (jwtSecret == null || jwtSecret.getBytes(StandardCharsets.UTF_8).length < 32) {
			throw new IllegalStateException("app.auth.jwt-secret(JWT_SECRET)은 32바이트 이상이어야 한다");
		}
		if (tokenTtl == null) {
			tokenTtl = Duration.ofHours(12);
		}
		if (loginMaxFailures <= 0) {
			loginMaxFailures = 10;
		}
		if (loginFailureWindow == null) {
			loginFailureWindow = Duration.ofMinutes(10);
		}
		if (maxPendingSignups <= 0) {
			maxPendingSignups = 50;
		}
	}
}
