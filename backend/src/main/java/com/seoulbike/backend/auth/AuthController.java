package com.seoulbike.backend.auth;

import java.time.Duration;
import java.time.Instant;
import java.util.UUID;

import jakarta.servlet.http.HttpServletRequest;
import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;
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

	/** loginId 길이를 막아 둔다 (DB 컬럼 64자). 로그인 실패 제한이 아이디를 메모리 키로 쓴다. */
	record LoginRequest(@NotBlank @Size(max = 64) String loginId, @NotBlank String password) {
	}

	record LoginResponse(String accessToken, String tokenType, long expiresIn, CurrentUser user) {
	}

	/** 손님 계정. 관리자(MANAGER) 권한 전부를 갖는다. 관리 화면에서 비활성화하면 손님 입장이 막힌다. */
	static final String GUEST_ID = "u-guest";
	static final String GUEST_LOGIN_ID = "guest";
	static final String GUEST_NAME = "손님 (로그인 없이)";

	private final UserRepository users;
	private final PasswordEncoder passwords;
	private final JwtEncoder jwt;
	private final AuthProperties props;
	private final LoginThrottle throttle;

	AuthController(UserRepository users, PasswordEncoder passwords, JwtEncoder jwt, AuthProperties props,
			LoginThrottle throttle) {
		this.users = users;
		this.passwords = passwords;
		this.jwt = jwt;
		this.props = props;
		this.throttle = throttle;
	}

	@PostMapping("/api/auth/login")
	LoginResponse login(@Valid @RequestBody LoginRequest req, HttpServletRequest http) {
		// 비밀번호를 보기 전에 시도를 센다. 막혀 있으면 맞는 비밀번호도 보지 않는다 (대입을 늦춘다).
		// getRemoteAddr()는 프록시(nginx) 뒤의 실제 접속 IP다 (server.forward-headers-strategy=native).
		String clientIp = http.getRemoteAddr();
		Duration blocked = throttle.tryAcquire(req.loginId(), clientIp);
		if (!blocked.isZero()) {
			throw new ApiException(HttpStatus.TOO_MANY_REQUESTS, "TOO_MANY_LOGIN_FAILURES",
					"비밀번호를 여러 번 틀려 잠시 막았다. " + (blocked.toMinutes() + 1) + "분 안에 다시 시도한다");
		}
		// 아이디가 없는 경우와 비밀번호가 틀린 경우를 구분하지 않는다.
		var stored = users.findByLoginId(req.loginId())
			.filter(u -> passwords.matches(req.password(), u.passwordHash()))
			.orElseThrow(() -> new ApiException(HttpStatus.UNAUTHORIZED, "INVALID_CREDENTIALS",
					"아이디 또는 비밀번호가 맞지 않다"));
		throttle.succeeded(req.loginId(), clientIp);
		// 상태는 비밀번호가 맞을 때만 알려준다. 남의 계정 상태를 떠보지 못하게 한다.
		requireActive(stored);
		return issue(stored);
	}

	/**
	 * 로그인 없이 들어오기. 비밀번호 없이 손님 계정(관리자 권한)의 토큰을 준다.
	 * 손님 계정은 처음 부를 때 만든다. 비밀번호는 아무도 모르는 무작위 값이라 아이디·비밀번호로는 들어올 수 없다.
	 * 끄는 법: GUEST_LOGIN_ENABLED=false로 다시 띄우거나, 계정 관리에서 손님 계정을 비활성화한다(이미 준 토큰도 바로 막힌다).
	 */
	@PostMapping("/api/auth/guest")
	LoginResponse guest() {
		if (!props.guestEnabled()) {
			throw ApiException.forbidden("GUEST_LOGIN_DISABLED", "로그인 없이 들어오기가 꺼져 있다. 계정으로 로그인한다");
		}
		// 처음 한 번만 만든다 (bcrypt가 느려 매번 해시하지 않는다).
		// 누가 먼저 "guest" 아이디로 가입했으면 그 계정이 아니라 손님 계정(u-guest)만 쓴다.
		var guest = users.findById(GUEST_ID).or(() -> {
			users.insertIfAbsent(GUEST_ID, GUEST_LOGIN_ID, passwords.encode(UUID.randomUUID().toString()), GUEST_NAME,
					Role.MANAGER, null, AccountStatus.ACTIVE);
			return users.findById(GUEST_ID);
		})
			.orElseThrow(() -> ApiException.conflict("GUEST_UNAVAILABLE",
					"손님 계정을 만들 수 없다 (아이디 guest를 다른 계정이 쓴다)"));
		requireActive(guest);
		return issue(guest);
	}

	@GetMapping("/api/me")
	CurrentUser me(@AuthenticationPrincipal Jwt token) {
		return CurrentUser.from(token);
	}

	private static void requireActive(UserRepository.StoredUser stored) {
		switch (stored.status()) {
			case ACTIVE -> {
			}
			case PENDING -> throw ApiException.forbidden("ACCOUNT_PENDING", "관리자 승인을 기다리는 계정이다");
			case DISABLED -> throw ApiException.forbidden("ACCOUNT_DISABLED", "비활성화된 계정이다");
		}
	}

	private LoginResponse issue(UserRepository.StoredUser stored) {
		var user = stored.toCurrentUser();
		Instant now = Instant.now();
		JwtClaimsSet.Builder claims = JwtClaimsSet.builder()
			.issuer("seoul-bike-backend")
			.subject(user.id())
			.issuedAt(now)
			.expiresAt(now.plus(props.tokenTtl()))
			.claim(CurrentUser.CLAIM_NAME, user.name())
			.claim(CurrentUser.CLAIM_ROLE, user.role().name())
			.claim(CurrentUser.CLAIM_TOKEN_VERSION, stored.tokenVersion());
		if (user.truckId() != null) {
			claims.claim(CurrentUser.CLAIM_TRUCK, user.truckId());
		}
		String token = jwt
			.encode(JwtEncoderParameters.from(JwsHeader.with(MacAlgorithm.HS256).build(), claims.build()))
			.getTokenValue();
		return new LoginResponse(token, "Bearer", props.tokenTtl().toSeconds(), user);
	}
}
