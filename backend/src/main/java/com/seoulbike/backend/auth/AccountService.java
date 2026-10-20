package com.seoulbike.backend.auth;

import java.util.List;
import java.util.UUID;

import org.springframework.http.HttpStatus;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

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

	AccountService(UserRepository users, PasswordEncoder passwords) {
		this.users = users;
		this.passwords = passwords;
	}

	Account signup(String loginId, String password, String name, Role requestedRole) {
		return insert(loginId, password, name, requestedRole, null, AccountStatus.PENDING);
	}

	Account create(String loginId, String password, String name, Role role, String truckId) {
		return insert(loginId, password, name, role, requireTruck(role, truckId), AccountStatus.ACTIVE);
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

	private Account insert(String loginId, String password, String name, Role role, String truckId,
			AccountStatus status) {
		String id = "u-" + UUID.randomUUID();
		if (!users.insertIfAbsent(id, loginId, passwords.encode(password), name, role, truckId, status)) {
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
