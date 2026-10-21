package com.seoulbike.backend.auth;

import java.time.Duration;
import java.time.Instant;
import java.util.concurrent.ConcurrentHashMap;

import org.springframework.stereotype.Component;

/**
 * 같은 아이디로 비밀번호를 계속 틀리면 잠시 막는다. 공개 주소라 비밀번호 무차별 대입을 늦춘다.
 *
 * <p>첫 실패부터 {@code window} 동안 {@code maxFailures}번 틀리면, 그 창이 끝날 때까지 맞는 비밀번호도 받지 않는다.
 * 없는 아이디도 똑같이 센다 (막히는 모습으로 아이디가 있는지 떠보지 못하게).
 * 주의: 아이디 기준이라 남이 일부러 틀려서 그 계정을 창 동안 막을 수 있다. 시연 규모에서는 단순한 쪽을 골랐다.
 * 메모리에만 두므로 backend가 다시 뜨면 초기화된다.
 */
@Component
class LoginThrottle {

	/** 이보다 많은 아이디가 쌓이면 창이 끝난 것부터 지운다. */
	private static final int PRUNE_ABOVE = 10_000;

	private record Failures(int count, Instant since) {
	}

	private final int maxFailures;
	private final Duration window;
	private final ConcurrentHashMap<String, Failures> failures = new ConcurrentHashMap<>();

	LoginThrottle(AuthProperties props) {
		this.maxFailures = props.loginMaxFailures();
		this.window = props.loginFailureWindow();
	}

	/** 막혀 있으면 풀릴 때까지 남은 시간, 아니면 {@link Duration#ZERO}. */
	Duration blockedFor(String loginId) {
		Failures f = failures.get(loginId);
		if (f == null || f.count() < maxFailures) {
			return Duration.ZERO;
		}
		Duration left = Duration.between(Instant.now(), f.since().plus(window));
		if (left.isNegative() || left.isZero()) {
			failures.remove(loginId, f);
			return Duration.ZERO;
		}
		return left;
	}

	void failed(String loginId) {
		Instant now = Instant.now();
		failures.compute(loginId,
				(id, f) -> f == null || expired(f, now) ? new Failures(1, now) : new Failures(f.count() + 1, f.since()));
		if (failures.size() > PRUNE_ABOVE) {
			failures.values().removeIf(f -> expired(f, now));
		}
	}

	void succeeded(String loginId) {
		failures.remove(loginId);
	}

	private boolean expired(Failures f, Instant now) {
		return !f.since().plus(window).isAfter(now);
	}
}
