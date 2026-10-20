package com.seoulbike.backend.auth;

import java.util.List;

import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Pattern;
import jakarta.validation.constraints.Size;
import org.springframework.http.HttpStatus;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.ResponseStatus;
import org.springframework.web.bind.annotation.RestController;

import com.seoulbike.backend.auth.UserRepository.Account;

/** 회원가입과 관리자 계정 관리. /api/admin/** 는 SecurityConfig에서 MANAGER만 연다. */
@RestController
class AccountController {

	private static final String LOGIN_ID = "^[a-z0-9._-]{4,32}$";

	record SignupRequest(@NotNull @Pattern(regexp = LOGIN_ID) String loginId,
			@NotNull @Size(min = 8, max = 72) String password, @NotBlank @Size(max = 50) String name,
			@NotNull Role requestedRole) {
	}

	record CreateAccountRequest(@NotNull @Pattern(regexp = LOGIN_ID) String loginId,
			@NotNull @Size(min = 8, max = 72) String password, @NotBlank @Size(max = 50) String name,
			@NotNull Role role, @Size(max = 64) String truckId) {
	}

	record ApproveAccountRequest(@NotNull Role role, @Size(max = 64) String truckId) {
	}

	private final AccountService accounts;

	AccountController(AccountService accounts) {
		this.accounts = accounts;
	}

	@PostMapping("/api/auth/signup")
	@ResponseStatus(HttpStatus.CREATED)
	Account signup(@Valid @RequestBody SignupRequest req) {
		return accounts.signup(req.loginId(), req.password(), req.name().trim(), req.requestedRole());
	}

	@GetMapping("/api/admin/users")
	List<Account> list(@RequestParam(required = false) AccountStatus status) {
		return accounts.list(status);
	}

	@PostMapping("/api/admin/users")
	@ResponseStatus(HttpStatus.CREATED)
	Account create(@Valid @RequestBody CreateAccountRequest req) {
		return accounts.create(req.loginId(), req.password(), req.name().trim(), req.role(), req.truckId());
	}

	@PostMapping("/api/admin/users/{userId}/approve")
	Account approve(@PathVariable String userId, @Valid @RequestBody ApproveAccountRequest req) {
		return accounts.approve(userId, req.role(), req.truckId());
	}

	@PostMapping("/api/admin/users/{userId}/disable")
	Account disable(@AuthenticationPrincipal Jwt jwt, @PathVariable String userId) {
		return accounts.disable(CurrentUser.from(jwt), userId);
	}
}
