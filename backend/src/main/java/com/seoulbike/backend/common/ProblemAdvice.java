package com.seoulbike.backend.common;

import java.util.stream.Collectors;
import java.util.stream.Stream;

import org.springframework.context.MessageSourceResolvable;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpStatus;
import org.springframework.http.HttpStatusCode;
import org.springframework.http.ProblemDetail;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.MethodArgumentNotValidException;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;
import org.springframework.web.context.request.WebRequest;
import org.springframework.web.method.annotation.HandlerMethodValidationException;
import org.springframework.web.servlet.mvc.method.annotation.ResponseEntityExceptionHandler;

/**
 * 오류 본문을 RFC 9457 problem+json으로 통일하고, 모든 오류에 계약 Problem의 code를 붙인다.
 * ResponseEntityExceptionHandler를 상속하므로 Spring Boot 기본 ProblemDetailsExceptionHandler는 꺼진다.
 */
@RestControllerAdvice
public class ProblemAdvice extends ResponseEntityExceptionHandler {

	@ExceptionHandler(ApiException.class)
	ProblemDetail api(ApiException e) {
		ProblemDetail problem = problem(e.status(), e.code(), e.getMessage());
		if (!e.suggestions().isEmpty()) {
			problem.setProperty("suggestions", e.suggestions());
		}
		return problem;
	}

	/** 요청 본문(@Valid @RequestBody) 검증 실패. 화면이 그대로 보여줄 수 있게 필드별로 적는다. */
	@Override
	protected ResponseEntity<Object> handleMethodArgumentNotValid(MethodArgumentNotValidException ex,
			HttpHeaders headers, HttpStatusCode status, WebRequest request) {
		String detail = ex.getBindingResult().getFieldErrors().stream()
			.map(f -> f.getField() + ": " + f.getDefaultMessage())
			.collect(Collectors.joining(", "));
		return handleExceptionInternal(ex, invalid(detail), headers, status, request);
	}

	/** 쿼리·경로 파라미터(@Min, @Max 등) 검증 실패. */
	@Override
	protected ResponseEntity<Object> handleHandlerMethodValidationException(HandlerMethodValidationException ex,
			HttpHeaders headers, HttpStatusCode status, WebRequest request) {
		String detail = ex.getParameterValidationResults().stream()
			.flatMap(r -> r.getResolvableErrors().stream()
				.map(e -> r.getMethodParameter().getParameterName() + ": " + e.getDefaultMessage()))
			.collect(Collectors.joining(", "));
		return handleExceptionInternal(ex, invalid(detail), headers, status, request);
	}

	/** 나머지 MVC 오류(404 경로 없음, 405, 깨진 JSON 등)에도 code를 붙인다. */
	@Override
	protected ResponseEntity<Object> createResponseEntity(Object body, HttpHeaders headers, HttpStatusCode status,
			WebRequest request) {
		if (body instanceof ProblemDetail p && (p.getProperties() == null || !p.getProperties().containsKey("code"))) {
			p.setProperty("code", status.value() == 400 ? "INVALID_REQUEST"
					: Stream.ofNullable(HttpStatus.resolve(status.value())).map(HttpStatus::name).findFirst().orElse("ERROR"));
		}
		return super.createResponseEntity(body, headers, status, request);
	}

	/** SecurityConfig의 401/403 응답도 같은 모양으로 만든다. */
	public static ProblemDetail problem(HttpStatus status, String code, String detail) {
		ProblemDetail problem = ProblemDetail.forStatusAndDetail(status, detail);
		problem.setProperty("code", code);
		return problem;
	}

	private static ProblemDetail invalid(String detail) {
		return problem(HttpStatus.BAD_REQUEST, "INVALID_REQUEST", detail.isEmpty() ? "요청 형식이 맞지 않다" : detail);
	}
}
