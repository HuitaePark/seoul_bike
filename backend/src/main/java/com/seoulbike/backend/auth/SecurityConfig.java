package com.seoulbike.backend.auth;

import java.io.IOException;
import java.nio.charset.StandardCharsets;

import javax.crypto.SecretKey;
import javax.crypto.spec.SecretKeySpec;

import com.nimbusds.jose.jwk.source.ImmutableSecret;
import jakarta.servlet.DispatcherType;
import jakarta.servlet.http.HttpServletResponse;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.core.annotation.Order;
import org.springframework.core.convert.converter.Converter;
import org.springframework.http.HttpMethod;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.security.authentication.AbstractAuthenticationToken;
import org.springframework.security.authentication.DisabledException;
import org.springframework.security.config.annotation.web.builders.HttpSecurity;
import org.springframework.security.config.http.SessionCreationPolicy;
import org.springframework.security.crypto.factory.PasswordEncoderFactories;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.security.oauth2.jose.jws.MacAlgorithm;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.security.oauth2.jwt.JwtDecoder;
import org.springframework.security.oauth2.jwt.JwtEncoder;
import org.springframework.security.oauth2.jwt.NimbusJwtDecoder;
import org.springframework.security.oauth2.jwt.NimbusJwtEncoder;
import org.springframework.security.oauth2.server.resource.authentication.JwtAuthenticationConverter;
import org.springframework.security.oauth2.server.resource.authentication.JwtGrantedAuthoritiesConverter;
import org.springframework.security.oauth2.server.resource.web.BearerTokenResolver;
import org.springframework.security.oauth2.server.resource.web.DefaultBearerTokenResolver;
import org.springframework.security.web.AuthenticationEntryPoint;
import org.springframework.security.web.SecurityFilterChain;
import org.springframework.security.web.access.AccessDeniedHandler;
import org.springframework.security.web.access.intercept.AuthorizationFilter;
import tools.jackson.databind.json.JsonMapper;

import com.seoulbike.backend.common.ProblemAdvice;

/**
 * 공개 API는 Bearer JWT(HS256)로 보호한다. 역할은 토큰의 role 클레임에서 ROLE_* 권한으로 바뀐다.
 * 권한 판단은 화면이 아니라 여기와 서비스 계층에서 한다 (과제 금지 사항: Vue의 권한 판단).
 */
@Configuration
@EnableConfigurationProperties(AuthProperties.class)
class SecurityConfig {

	/**
	 * business-mcp가 부르는 /internal/** 는 사용자 JWT가 아니라 서비스 토큰(X-Internal-Token)으로만 연다.
	 * 사용자 체인보다 먼저 걸려 /internal/** 요청은 아래 api 체인에 가지 않는다.
	 */
	@Bean
	@Order(1)
	SecurityFilterChain internal(HttpSecurity http, JsonMapper json, @Value("${app.internal.token:}") String token)
			throws Exception {
		AuthenticationEntryPoint unauthorized = (req, res, e) -> writeProblem(res, json, HttpStatus.UNAUTHORIZED,
				"UNAUTHORIZED", InternalTokenFilter.HEADER + "이 없거나 맞지 않다");
		return http.securityMatcher("/internal/**")
			.csrf(csrf -> csrf.disable())
			.sessionManagement(s -> s.sessionCreationPolicy(SessionCreationPolicy.STATELESS))
			.addFilterBefore(new InternalTokenFilter(token), AuthorizationFilter.class)
			.authorizeHttpRequests(auth -> auth
				.dispatcherTypeMatchers(DispatcherType.ERROR).permitAll()
				.anyRequest().hasRole(InternalTokenFilter.ROLE))
			.exceptionHandling(e -> e.authenticationEntryPoint(unauthorized))
			.build();
	}

	@Bean
	@Order(2)
	SecurityFilterChain api(HttpSecurity http, JsonMapper json, UserRepository users) throws Exception {
		AuthenticationEntryPoint unauthorized = (req, res, e) -> writeProblem(res, json, HttpStatus.UNAUTHORIZED,
				"UNAUTHORIZED", e instanceof DisabledException ? "계정이 활성 상태가 아니다. 다시 로그인한다" : "로그인이 필요하다");
		AccessDeniedHandler forbidden = (req, res, e) -> writeProblem(res, json, HttpStatus.FORBIDDEN, "FORBIDDEN",
				"권한이 없다");
		return http
			.csrf(csrf -> csrf.disable())
			.sessionManagement(s -> s.sessionCreationPolicy(SessionCreationPolicy.STATELESS))
			.authorizeHttpRequests(auth -> auth
				// SSE는 비동기 디스패치로 응답을 이어 쓴다. 첫 요청에서 이미 인증했으므로 다시 막지 않는다.
				.dispatcherTypeMatchers(DispatcherType.ASYNC, DispatcherType.ERROR).permitAll()
				.requestMatchers(HttpMethod.POST, "/api/auth/login", "/api/auth/signup").permitAll()
				.requestMatchers("/api/admin/**").hasRole(Role.MANAGER.name())
				.requestMatchers(HttpMethod.POST, "/api/reports").hasRole(Role.DRIVER.name())
				.requestMatchers(HttpMethod.GET, "/api/drivers/me/route").hasRole(Role.DRIVER.name())
				// 근무조 계획·트럭 상태에는 모든 트럭이 있다. 기사는 자기 경로(/api/drivers/me/route)만 본다 (NFR-06).
				.requestMatchers(HttpMethod.GET, "/api/shifts/*/plan", "/api/shifts/*/trucks", "/api/shifts/*/safety")
				.hasAnyRole(Role.MANAGER.name(), Role.EXECUTIVE.name())
				.requestMatchers(HttpMethod.GET, "/", "/index.html", "/swagger", "/swagger/**", "/specs/**",
						"/webjars/**", "/actuator/health/**", "/api/system/status")
				.permitAll()
				.anyRequest().authenticated())
			// 토큰이 틀린 경우(리소스 서버)와 토큰이 아예 없는 경우(exceptionHandling) 모두 같은 본문을 쓴다.
			// 로그인·가입은 토큰을 보지 않는다. 브라우저에 남은 옛 토큰(비활성 계정 등)이 붙어 오면 그 토큰 때문에
			// 401이 나서 다시 로그인할 수 없게 된다.
			.oauth2ResourceServer(o -> o.bearerTokenResolver(ignoringAuthEndpoints())
				.jwt(jwt -> jwt.jwtAuthenticationConverter(activeAccountsOnly(users)))
				.authenticationEntryPoint(unauthorized)
				.accessDeniedHandler(forbidden))
			.exceptionHandling(e -> e.authenticationEntryPoint(unauthorized).accessDeniedHandler(forbidden))
			.build();
	}

	private static BearerTokenResolver ignoringAuthEndpoints() {
		DefaultBearerTokenResolver header = new DefaultBearerTokenResolver();
		return req -> req.getRequestURI().startsWith("/api/auth/") ? null : header.resolve(req);
	}

	@Bean
	SecretKey jwtKey(AuthProperties props) {
		return new SecretKeySpec(props.jwtSecret().getBytes(StandardCharsets.UTF_8), "HmacSHA256");
	}

	@Bean
	JwtEncoder jwtEncoder(SecretKey jwtKey) {
		return new NimbusJwtEncoder(new ImmutableSecret<>(jwtKey));
	}

	@Bean
	JwtDecoder jwtDecoder(SecretKey jwtKey) {
		return NimbusJwtDecoder.withSecretKey(jwtKey).macAlgorithm(MacAlgorithm.HS256).build();
	}

	@Bean
	PasswordEncoder passwordEncoder() {
		return PasswordEncoderFactories.createDelegatingPasswordEncoder();
	}

	private static JwtAuthenticationConverter roleConverter() {
		JwtGrantedAuthoritiesConverter authorities = new JwtGrantedAuthoritiesConverter();
		authorities.setAuthoritiesClaimName(CurrentUser.CLAIM_ROLE);
		authorities.setAuthorityPrefix("ROLE_");
		JwtAuthenticationConverter converter = new JwtAuthenticationConverter();
		converter.setJwtGrantedAuthoritiesConverter(authorities);
		return converter;
	}

	/**
	 * 토큰이 아직 살아 있어도 계정이 ACTIVE가 아니면(비활성화, 삭제) 바로 막는다. 토큰 만료(최대 12시간)를 기다리지 않는다.
	 * 토큰 버전(tv)이 계정과 다르면 비활성화 전에 받은 토큰이라 다시 열린 계정이어도 막는다. tv가 없는 토큰은 0으로 본다.
	 * 요청마다 기본 키 조회 한 번이다. 이미 열린 SSE 연결은 끊지 않는다 (작업이 끝나면 닫힌다).
	 */
	private static Converter<Jwt, AbstractAuthenticationToken> activeAccountsOnly(UserRepository users) {
		JwtAuthenticationConverter roles = roleConverter();
		return jwt -> {
			Number claimed = jwt.getClaim(CurrentUser.CLAIM_TOKEN_VERSION);
			int version = claimed == null ? 0 : claimed.intValue();
			if (users.tokenStateOf(jwt.getSubject())
				.filter(s -> s.status() == AccountStatus.ACTIVE && s.tokenVersion() == version)
				.isEmpty()) {
				throw new DisabledException("계정이 활성 상태가 아니거나 비활성화 전에 받은 토큰이다");
			}
			return roles.convert(jwt);
		};
	}

	private static void writeProblem(HttpServletResponse res, JsonMapper json, HttpStatus status, String code,
			String detail) throws IOException {
		res.setStatus(status.value());
		res.setContentType(MediaType.APPLICATION_PROBLEM_JSON_VALUE);
		res.setCharacterEncoding(StandardCharsets.UTF_8.name());
		json.writeValue(res.getOutputStream(), ProblemAdvice.problem(status, code, detail));
	}
}
