package com.seoulbike.backend.auth;

import java.time.Instant;

import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;
import org.springframework.http.HttpStatus;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.security.oauth2.jose.jws.MacAlgorithm;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.security.oauth2.jwt.JwsHeader;
import org.springframework.security.oauth2.jwt.JwtClaimsSet;
import org.springframework.security.oauth2.jwt.JwtEncoder;
import org.springframework.security.oauth2.jwt.JwtEncoderParameters;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RestController;

import com.seoulbike.backend.common.ApiException;

@RestController
class AuthController {

	record LoginRequest(@NotBlank String loginId, @NotBlank String password) {
	}

	record LoginResponse(String accessToken, String tokenType, long expiresIn, CurrentUser user) {
	}

	private final UserRepository users;
	private final PasswordEncoder passwords;
	private final JwtEncoder jwt;
	private final AuthProperties props;

	AuthController(UserRepository users, PasswordEncoder passwords, JwtEncoder jwt, AuthProperties props) {
		this.users = users;
		this.passwords = passwords;
		this.jwt = jwt;
		this.props = props;
	}

	@PostMapping("/api/auth/login")
	LoginResponse login(@Valid @RequestBody LoginRequest req) {
		// 아이디가 없는 경우와 비밀번호가 틀린 경우를 구분하지 않는다.
		var stored = users.findByLoginId(req.loginId())
			.filter(u -> passwords.matches(req.password(), u.passwordHash()))
			.orElseThrow(() -> new ApiException(HttpStatus.UNAUTHORIZED, "INVALID_CREDENTIALS",
					"아이디 또는 비밀번호가 맞지 않다"));
		// 상태는 비밀번호가 맞을 때만 알려준다. 남의 계정 상태를 떠보지 못하게 한다.
		switch (stored.status()) {
			case ACTIVE -> {
			}
			case PENDING -> throw ApiException.forbidden("ACCOUNT_PENDING", "관리자 승인을 기다리는 계정이다");
			case DISABLED -> throw ApiException.forbidden("ACCOUNT_DISABLED", "비활성화된 계정이다");
		}
		var user = stored.toCurrentUser();

		Instant now = Instant.now();
		JwtClaimsSet.Builder claims = JwtClaimsSet.builder()
			.issuer("seoul-bike-backend")
			.subject(user.id())
			.issuedAt(now)
			.expiresAt(now.plus(props.tokenTtl()))
			.claim(CurrentUser.CLAIM_NAME, user.name())
			.claim(CurrentUser.CLAIM_ROLE, user.role().name());
		if (user.truckId() != null) {
			claims.claim(CurrentUser.CLAIM_TRUCK, user.truckId());
		}
		String token = jwt
			.encode(JwtEncoderParameters.from(JwsHeader.with(MacAlgorithm.HS256).build(), claims.build()))
			.getTokenValue();
		return new LoginResponse(token, "Bearer", props.tokenTtl().toSeconds(), user);
	}

	@GetMapping("/api/me")
	CurrentUser me(@AuthenticationPrincipal Jwt token) {
		return CurrentUser.from(token);
	}
}
