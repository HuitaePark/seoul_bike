package com.seoulbike.backend.auth;

import java.time.Duration;
import java.time.Instant;
import java.util.concurrent.ConcurrentHashMap;

import org.springframework.stereotype.Component;

/**
 * 같은 아이디로 비밀번호를 계속 틀리면 잠시 막는다. 공개 주소라 비밀번호 무차별 대입을 늦춘다.
 *
 * <p>첫 시도부터 {@code window} 동안 {@code maxAttempts}번까지만 비밀번호를 본다. 맞히면 초기화하고, 다 쓰면 창이 끝날 때까지
 * 맞는 비밀번호도 받지 않는다. 시도는 비밀번호를 보기 전에 센다 (한꺼번에 몰아 보낸 요청도 한도를 넘지 못하게).
 * 없는 아이디도 똑같이 센다 (막히는 모습으로 아이디가 있는지 떠보지 못하게).
 * 주의: 아이디 기준이라 남이 일부러 틀려서 그 계정을 창 동안 막을 수 있다. 시연 규모에서는 단순한 쪽을 골랐다.
 * 메모리에만 두므로 backend가 다시 뜨면 초기화된다.
 */
@Component
class LoginThrottle {

	/** 아이디가 이보다 많이 쌓이면, 창이 끝났거나 아직 막히지 않은 것부터 지운다 (막힌 아이디는 남긴다). */
	private static final int MAX_KEYS = 10_000;

	private record Attempts(int count, Instant since) {
	}

	private final int maxAttempts;
	private final Duration window;
	private final ConcurrentHashMap<String, Attempts> attempts = new ConcurrentHashMap<>();

	LoginThrottle(AuthProperties props) {
		this.maxAttempts = props.loginMaxFailures();
		this.window = props.loginFailureWindow();
	}

	/**
	 * 비밀번호를 보기 전에 부른다. 막혀 있으면 풀릴 때까지 남은 시간을 돌려준다. 아니면 시도 하나를 세고
	 * {@link Duration#ZERO}를 돌려준다. 확인과 세기가 한 번에 일어난다.
	 */
	Duration tryAcquire(String loginId) {
		Instant now = Instant.now();
		Duration[] blocked = { Duration.ZERO };
		attempts.compute(loginId, (id, a) -> {
			if (a == null || expired(a, now)) {
				return new Attempts(1, now);
			}
			if (a.count() >= maxAttempts) {
				blocked[0] = Duration.between(now, a.since().plus(window));
				return a;
			}
			return new Attempts(a.count() + 1, a.since());
		});
		if (attempts.size() > MAX_KEYS) {
			attempts.values().removeIf(a -> expired(a, now) || a.count() < maxAttempts);
		}
		return blocked[0];
	}

	void succeeded(String loginId) {
		attempts.remove(loginId);
	}

	private boolean expired(Attempts a, Instant now) {
		return !a.since().plus(window).isAfter(now);
	}
}
