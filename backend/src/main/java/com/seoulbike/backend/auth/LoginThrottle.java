package com.seoulbike.backend.auth;

import java.time.Duration;
import java.time.Instant;
import java.util.concurrent.ConcurrentHashMap;

import org.springframework.stereotype.Component;

/**
 * 같은 아이디를 같은 곳(접속 IP)에서 계속 틀리면 잠시 막는다. 공개 주소라 비밀번호 무차별 대입을 늦춘다.
 *
 * <p>첫 시도부터 {@code window} 동안 {@code maxAttempts}번까지만 비밀번호를 본다. 맞히면 초기화하고, 다 쓰면 창이 끝날 때까지
 * 맞는 비밀번호도 받지 않는다. 시도는 비밀번호를 보기 전에 센다 (한꺼번에 몰아 보낸 요청도 한도를 넘지 못하게).
 * 없는 아이디도 똑같이 센다 (막히는 모습으로 아이디가 있는지 떠보지 못하게).
 *
 * <p>IP까지 묶는 이유: 아이디만으로 세면 남이 일부러 틀려서 그 계정(예 manager)을 모두에게 막을 수 있다. IP를 묶으면 막히는 건
 * 틀린 사람의 IP뿐이다. 대신 IP를 바꿔 가며 시도하면 IP마다 한도만큼 시도할 수 있다.
 * 접속 IP는 공개 주소(Tailscale Funnel)가 X-Forwarded-For에 덮어써 넣은 값이다 ({@code server.forward-headers-strategy}).
 * 메모리에만 두므로 backend가 다시 뜨면 초기화된다.
 */
@Component
class LoginThrottle {

	/**
	 * 이만큼까지만 기억한다 (약 20MB). 넘으면 창이 끝난 것만 지우고, 그래도 넘으면 처음 보는 (아이디, IP)는 막는다.
	 * 아직 막히지 않은 항목을 지우면 가짜 아이디를 쏟아 넣어 노리는 아이디의 횟수를 초기화할 수 있기 때문이다.
	 * 대신 그렇게 넘친 동안(최대 창 길이)에는 처음 로그인하는 사람도 429를 받는다.
	 */
	private static final int MAX_KEYS = 100_000;

	private record Key(String loginId, String clientIp) {
	}

	private record Attempts(int count, Instant since) {
	}

	private final int maxAttempts;
	private final Duration window;
	private final ConcurrentHashMap<Key, Attempts> attempts = new ConcurrentHashMap<>();

	LoginThrottle(AuthProperties props) {
		this.maxAttempts = props.loginMaxFailures();
		this.window = props.loginFailureWindow();
	}

	/**
	 * 비밀번호를 보기 전에 부른다. 막혀 있으면 풀릴 때까지 남은 시간을 돌려준다. 아니면 시도 하나를 세고
	 * {@link Duration#ZERO}를 돌려준다. 확인과 세기가 한 번에 일어난다.
	 */
	Duration tryAcquire(String loginId, String clientIp) {
		Key key = new Key(loginId, clientIp);
		Instant now = Instant.now();
		if (attempts.size() >= MAX_KEYS && !attempts.containsKey(key)) {
			attempts.values().removeIf(a -> expired(a, now));
			if (attempts.size() >= MAX_KEYS) {
				return window;
			}
		}
		Duration[] blocked = { Duration.ZERO };
		attempts.compute(key, (k, a) -> {
			if (a == null || expired(a, now)) {
				return new Attempts(1, now);
			}
			if (a.count() >= maxAttempts) {
				blocked[0] = Duration.between(now, a.since().plus(window));
				return a;
			}
			return new Attempts(a.count() + 1, a.since());
		});
		return blocked[0];
	}

	void succeeded(String loginId, String clientIp) {
		attempts.remove(new Key(loginId, clientIp));
	}

	private boolean expired(Attempts a, Instant now) {
		return !a.since().plus(window).isAfter(now);
	}
}
