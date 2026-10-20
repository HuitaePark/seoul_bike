package com.seoulbike.backend.common;

import org.springframework.http.HttpStatus;
import org.springframework.http.ProblemDetail;
import org.springframework.web.bind.MethodArgumentNotValidException;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;

/** 오류 본문을 RFC 9457 problem+json으로 통일한다 (계약 Problem 스키마). */
@RestControllerAdvice
class ProblemAdvice {

	@ExceptionHandler(ApiException.class)
	ProblemDetail api(ApiException e) {
		return problem(e.status(), e.code(), e.getMessage());
	}

	@ExceptionHandler(MethodArgumentNotValidException.class)
	ProblemDetail invalid(MethodArgumentNotValidException e) {
		String detail = e.getBindingResult().getFieldErrors().stream()
			.map(f -> f.getField() + ": " + f.getDefaultMessage())
			.reduce((a, b) -> a + ", " + b)
			.orElse("요청 형식이 맞지 않다");
		return problem(HttpStatus.BAD_REQUEST, "INVALID_REQUEST", detail);
	}

	static ProblemDetail problem(HttpStatus status, String code, String detail) {
		ProblemDetail problem = ProblemDetail.forStatusAndDetail(status, detail);
		problem.setProperty("code", code);
		return problem;
	}
}
