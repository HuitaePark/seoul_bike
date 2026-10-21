package com.seoulbike.backend.auth;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.List;

import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.springframework.security.authentication.UsernamePasswordAuthenticationToken;
import org.springframework.security.core.authority.SimpleGrantedAuthority;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.web.filter.OncePerRequestFilter;

/**
 * /internal/** 의 서비스 토큰(X-Internal-Token, internal-api securitySchemes). 맞으면 ROLE_INTERNAL로 인증한다.
 * 비교 시간이 값에 따라 달라지지 않게 하고, 설정한 토큰이 비어 있으면 아무 요청도 통과시키지 않는다.
 */
final class InternalTokenFilter extends OncePerRequestFilter {

	static final String HEADER = "X-Internal-Token";
	static final String ROLE = "INTERNAL";

	private final byte[] expected;

	InternalTokenFilter(String token) {
		this.expected = token == null ? new byte[0] : token.getBytes(StandardCharsets.UTF_8);
	}

	@Override
	protected void doFilterInternal(HttpServletRequest request, HttpServletResponse response, FilterChain chain)
			throws ServletException, IOException {
		String given = request.getHeader(HEADER);
		if (expected.length > 0 && given != null
				&& MessageDigest.isEqual(expected, given.getBytes(StandardCharsets.UTF_8))) {
			var context = SecurityContextHolder.createEmptyContext();
			context.setAuthentication(UsernamePasswordAuthenticationToken.authenticated("business-mcp", null,
					List.of(new SimpleGrantedAuthority("ROLE_" + ROLE))));
			SecurityContextHolder.setContext(context);
		}
		chain.doFilter(request, response);
	}
}
