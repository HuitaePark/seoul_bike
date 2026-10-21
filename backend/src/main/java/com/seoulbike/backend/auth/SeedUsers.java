package com.seoulbike.backend.auth;

import java.util.List;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.ApplicationArguments;
import org.springframework.boot.ApplicationRunner;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.stereotype.Component;

/**
 * 시연·통합 테스트용 계정. 비밀번호는 저장소가 아니라 서버 .env(SEED_USER_PASSWORD)에서만 온다.
 * 값이 비어 있으면 아무 계정도 만들지 않는다. 이미 있는 계정은 건드리지 않는다.
 */
@Component
class SeedUsers implements ApplicationRunner {

	private static final Logger log = LoggerFactory.getLogger(SeedUsers.class);

	private final UserRepository users;
	private final PasswordEncoder passwords;
	private final AuthProperties props;

	SeedUsers(UserRepository users, PasswordEncoder passwords, AuthProperties props) {
		this.users = users;
		this.passwords = passwords;
		this.props = props;
	}

	@Override
	public void run(ApplicationArguments args) {
		if (props.seedPassword() == null || props.seedPassword().isBlank()) {
			return;
		}
		String hash = passwords.encode(props.seedPassword());
		record Seed(String id, String loginId, String name, Role role, String truckId) {
		}
		var seeds = List.of(new Seed("u-manager", "manager", "관제 관리자", Role.MANAGER, null),
				new Seed("u-driver-1", "driver1", "기사 1", Role.DRIVER, "TR-01"),
				new Seed("u-driver-2", "driver2", "기사 2", Role.DRIVER, "TR-02"),
				new Seed("u-executive", "executive", "운영사 책임자", Role.EXECUTIVE, null));
		long created = seeds.stream()
			.filter(s -> users.insertIfAbsent(s.id(), s.loginId(), hash, s.name(), s.role(), s.truckId(), AccountStatus.ACTIVE))
			.count();
		if (created > 0) {
			log.info("시드 계정 {}개를 만들었다", created);
		}
	}
}
