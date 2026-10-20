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
		var seeds = List.of(
				new UserRepository.StoredUser("u-manager", "manager", hash, "관제 관리자", Role.MANAGER, null),
				new UserRepository.StoredUser("u-driver-1", "driver1", hash, "기사 1", Role.DRIVER, "truck-1"),
				new UserRepository.StoredUser("u-driver-2", "driver2", hash, "기사 2", Role.DRIVER, "truck-2"),
				new UserRepository.StoredUser("u-executive", "executive", hash, "운영사 책임자", Role.EXECUTIVE, null));
		long created = seeds.stream().filter(users::insertIfAbsent).count();
		if (created > 0) {
			log.info("시드 계정 {}개를 만들었다", created);
		}
	}
}
