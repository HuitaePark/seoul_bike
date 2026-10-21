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
 * 관리자는 바로 쓸 수 있는 계정을 발급하거나 계정을 비활성화하고 다시 열 수 있다.
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

	/**
	 * 비활성 계정을 다시 연다. 역할·트럭은 비활성화 전 그대로다.
	 * 승인된 적 있는 계정만 연다: 가입 거절도 DISABLED로 남는데, 그걸 열면 신청 때 고른 역할(관리자 포함)로
	 * 승인 단계 없이 ACTIVE가 된다. 비활성화 전에 받은 토큰은 토큰 버전이 달라 다시 열어도 쓰지 못한다.
	 */
	@Transactional
	Account enable(String userId) {
		var user = find(userId);
		if (user.status() != AccountStatus.DISABLED) {
			throw ApiException.conflict("ACCOUNT_NOT_DISABLED", "비활성 상태가 아니다 (현재 " + user.status() + ")");
		}
		if (user.approvedAt() == null) {
			throw ApiException.conflict("SIGNUP_REJECTED",
					"승인된 적 없는 계정(거절한 가입 신청)이라 다시 열 수 없다. 다시 가입을 받거나 새로 발급한다");
		}
		if (!users.enable(userId)) {
			throw ApiException.conflict("ACCOUNT_NOT_DISABLED", "비활성 상태가 아니다");
		}
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

	/**
	 * 기사는 트럭이 있어야 경로·승인을 받을 수 있다. 다른 역할의 트럭 값은 버린다.
	 * 트럭은 트럭 목록(ref.trucks)의 활성 트럭이어야 한다 (합의 30). 오타 난 트럭으로 승인하면 그 기사는 경로를 영영 받지 못한다.
	 */
	private String requireTruck(Role role, String truckId) {
		if (role != Role.DRIVER) {
			return null;
		}
		if (truckId == null || truckId.isBlank()) {
			throw new ApiException(HttpStatus.BAD_REQUEST, "TRUCK_REQUIRED", "기사 계정은 truckId가 필요하다");
		}
		String truck = truckId.trim();
		List<String> trucks = users.activeTruckIds();
		if (!trucks.contains(truck)) {
			// 구역 ID처럼 고를 수 있는 트럭 ID를 suggestions로 준다 (서울 전체 35대 안팎).
			throw new ApiException(HttpStatus.BAD_REQUEST, "TRUCK_NOT_FOUND",
					"트럭 " + truck + "이 트럭 목록에 없거나 운행하지 않는다. 트럭 ID는 TR-NN 꼴이다 (예: TR-01)", trucks);
		}
		return truck;
	}
}
