package com.seoulbike.backend.auth;

import java.nio.charset.StandardCharsets;
import java.time.Duration;

import org.springframework.boot.context.properties.ConfigurationProperties;

/**
 * @param jwtSecret    HS256 서명 키. 32바이트 미만이면 기동을 막는다 (서버 .env의 JWT_SECRET)
 * @param tokenTtl     액세스 토큰 유효 시간
 * @param seedPassword 비어 있지 않으면 기동할 때 시드 계정(관리자·기사·책임자)을 만든다
 */
@ConfigurationProperties("app.auth")
record AuthProperties(String jwtSecret, Duration tokenTtl, String seedPassword) {

	AuthProperties {
		if (jwtSecret == null || jwtSecret.getBytes(StandardCharsets.UTF_8).length < 32) {
			throw new IllegalStateException("app.auth.jwt-secret(JWT_SECRET)은 32바이트 이상이어야 한다");
		}
		if (tokenTtl == null) {
			tokenTtl = Duration.ofHours(12);
		}
	}
}
