package com.seoulbike.backend.auth;

import java.util.List;
import java.util.UUID;

import org.springframework.http.HttpStatus;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.transaction.support.TransactionTemplate;

import com.seoulbike.backend.auth.UserRepository.Account;
import com.seoulbike.backend.common.ApiException;

/**
 * 회원가입은 승인 대기(PENDING)로 들어오고, 관리자(MANAGER)가 역할·트럭을 확정해 ACTIVE로 바꾼다.
 * 관리자는 바로 쓸 수 있는 계정을 발급하거나 계정을 비활성화할 수 있다.
 */
@Service
class AccountService {

	private final UserRepository users;
	private final PasswordEncoder passwords;
	private final TransactionTemplate tx;
	private final int maxPendingSignups;

	AccountService(UserRepository users, PasswordEncoder passwords, TransactionTemplate tx, AuthProperties props) {
		this.users = users;
		this.passwords = passwords;
		this.tx = tx;
		this.maxPendingSignups = props.maxPendingSignups();
	}

	/**
	 * 누구나 부를 수 있으므로 승인 대기가 너무 쌓이면 받지 않는다 (가입 도배로 표가 끝없이 늘지 않게).
	 * 가입 신청을 DB 잠금으로 한 줄로 세워, 몰아 보낸 신청도 상한을 넘지 않게 한다.
	 * 비밀번호 해시(bcrypt)는 잠금과 DB 연결을 잡기 전에 만든다.
	 */
	Account signup(String loginId, String password, String name, Role requestedRole) {
		String hash = passwords.encode(password);
		return tx.execute(status -> {
			users.lockSignups();
			if (users.countPending() >= maxPendingSignups) {
				throw new ApiException(HttpStatus.TOO_MANY_REQUESTS, "TOO_MANY_PENDING_SIGNUPS",
						"승인을 기다리는 가입 신청이 너무 많다. 관리자가 처리한 뒤 다시 신청한다");
			}
			return insert(loginId, hash, name, requestedRole, null, AccountStatus.PENDING);
		});
	}

	Account create(String loginId, String password, String name, Role role, String truckId) {
		return insert(loginId, passwords.encode(password), name, role, requireTruck(role, truckId), AccountStatus.ACTIVE);
	}

	List<Account> list(AccountStatus status) {
		return users.list(status).stream().map(UserRepository.StoredUser::toAccount).toList();
	}

	@Transactional
	Account approve(String userId, Role role, String truckId) {
		var user = find(userId);
		if (user.status() != AccountStatus.PENDING || !users.approve(userId, role, requireTruck(role, truckId))) {
			throw ApiException.conflict("ACCOUNT_NOT_PENDING", "승인 대기 상태가 아니다 (현재 " + user.status() + ")");
		}
		return find(userId).toAccount();
	}

	@Transactional
	Account disable(CurrentUser by, String userId) {
		if (by.id().equals(userId)) {
			throw ApiException.forbidden("CANNOT_DISABLE_SELF", "자기 계정은 비활성화할 수 없다");
		}
		find(userId);
		users.disable(userId);
		return find(userId).toAccount();
	}

	private Account insert(String loginId, String passwordHash, String name, Role role, String truckId,
			AccountStatus status) {
		String id = "u-" + UUID.randomUUID();
		if (!users.insertIfAbsent(id, loginId, passwordHash, name, role, truckId, status)) {
			throw ApiException.conflict("LOGIN_ID_TAKEN", "이미 쓰는 아이디다: " + loginId);
		}
		return find(id).toAccount();
	}

	private UserRepository.StoredUser find(String userId) {
		return users.findById(userId).orElseThrow(() -> ApiException.notFound("ACCOUNT_NOT_FOUND", "계정 " + userId + "가 없다"));
	}

	/** 기사는 트럭이 있어야 경로·승인을 받을 수 있다. 다른 역할의 트럭 값은 버린다. */
	private static String requireTruck(Role role, String truckId) {
		if (role != Role.DRIVER) {
			return null;
		}
		if (truckId == null || truckId.isBlank()) {
			throw new ApiException(HttpStatus.BAD_REQUEST, "TRUCK_REQUIRED", "기사 계정은 truckId가 필요하다");
		}
		return truckId.trim();
	}
}
