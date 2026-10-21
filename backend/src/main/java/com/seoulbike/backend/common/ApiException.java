package com.seoulbike.backend.common;

import java.util.List;

import org.springframework.http.HttpStatus;

/** 계약의 Problem.code를 함께 실어 보내는 업무 예외. 화면은 code로 분기한다. */
public class ApiException extends RuntimeException {

	private final HttpStatus status;
	private final String code;
	private final List<String> suggestions;

	public ApiException(HttpStatus status, String code, String detail) {
		this(status, code, detail, List.of());
	}

	/** suggestions: 계약 ToolProblem.suggestions. 모델이 이 값으로 다시 호출할 수 있게 고칠 후보 ID를 준다. */
	public ApiException(HttpStatus status, String code, String detail, List<String> suggestions) {
		super(detail);
		this.status = status;
		this.code = code;
		this.suggestions = List.copyOf(suggestions);
	}

	public HttpStatus status() {
		return status;
	}

	public String code() {
		return code;
	}

	public List<String> suggestions() {
		return suggestions;
	}

	public static ApiException notFound(String code, String detail) {
		return new ApiException(HttpStatus.NOT_FOUND, code, detail);
	}

	public static ApiException notFound(String code, String detail, List<String> suggestions) {
		return new ApiException(HttpStatus.NOT_FOUND, code, detail, suggestions);
	}

	public static ApiException forbidden(String code, String detail) {
		return new ApiException(HttpStatus.FORBIDDEN, code, detail);
	}

	public static ApiException conflict(String code, String detail) {
		return new ApiException(HttpStatus.CONFLICT, code, detail);
	}
}
