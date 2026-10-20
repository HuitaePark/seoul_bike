package com.seoulbike.backend.auth;

import static org.assertj.core.api.Assertions.assertThat;

import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;
import org.springframework.mock.web.MockFilterChain;
import org.springframework.mock.web.MockHttpServletRequest;
import org.springframework.mock.web.MockHttpServletResponse;
import org.springframework.security.core.Authentication;
import org.springframework.security.core.context.SecurityContextHolder;

/** 서비스 토큰: 설정이 비어 있으면 빈 헤더를 포함해 아무것도 통과하지 않는다. */
class InternalTokenFilterTest {

	@AfterEach
	void clear() {
		SecurityContextHolder.clearContext();
	}

	@Test
	void emptyConfiguredTokenLetsNothingThrough() throws Exception {
		assertThat(authenticate("", "")).isNull();
		assertThat(authenticate("", null)).isNull();
		assertThat(authenticate(null, "")).isNull();
	}

	@Test
	void onlyTheExactTokenAuthenticatesAsInternal() throws Exception {
		assertThat(authenticate("secret-token", "secret-tokeN")).isNull();
		assertThat(authenticate("secret-token", "secret-token-longer")).isNull();
		Authentication ok = authenticate("secret-token", "secret-token");
		assertThat(ok.getAuthorities()).extracting(Object::toString).containsExactly("ROLE_INTERNAL");
	}

	private Authentication authenticate(String configured, String header) throws Exception {
		SecurityContextHolder.clearContext();
		var request = new MockHttpServletRequest("GET", "/internal/shifts/s/reports");
		if (header != null) {
			request.addHeader(InternalTokenFilter.HEADER, header);
		}
		new InternalTokenFilter(configured).doFilter(request, new MockHttpServletResponse(), new MockFilterChain());
		return SecurityContextHolder.getContext().getAuthentication();
	}
}
