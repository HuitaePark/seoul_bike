package com.seoulbike.backend.auth;

import java.nio.charset.StandardCharsets;

import javax.crypto.SecretKey;
import javax.crypto.spec.SecretKeySpec;

import com.nimbusds.jose.jwk.source.ImmutableSecret;
import jakarta.servlet.DispatcherType;
import jakarta.servlet.http.HttpServletResponse;
import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.http.HttpMethod;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.http.ProblemDetail;
import org.springframework.security.config.annotation.web.builders.HttpSecurity;
import org.springframework.security.config.http.SessionCreationPolicy;
import org.springframework.security.crypto.factory.PasswordEncoderFactories;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.security.oauth2.jose.jws.MacAlgorithm;
import org.springframework.security.oauth2.jwt.JwtDecoder;
import org.springframework.security.oauth2.jwt.JwtEncoder;
import org.springframework.security.oauth2.jwt.NimbusJwtDecoder;
import org.springframework.security.oauth2.jwt.NimbusJwtEncoder;
import org.springframework.security.oauth2.server.resource.authentication.JwtAuthenticationConverter;
import org.springframework.security.oauth2.server.resource.authentication.JwtGrantedAuthoritiesConverter;
import org.springframework.security.web.SecurityFilterChain;
import tools.jackson.databind.json.JsonMapper;

/**
 * 공개 API는 Bearer JWT(HS256)로 보호한다. 역할은 토큰의 role 클레임에서 ROLE_* 권한으로 바뀐다.
 * 권한 판단은 화면이 아니라 여기와 서비스 계층에서 한다 (과제 금지 사항: Vue의 권한 판단).
 */
@Configuration
@EnableConfigurationProperties(AuthProperties.class)
class SecurityConfig {

	@Bean
	SecurityFilterChain api(HttpSecurity http, JsonMapper json) throws Exception {
		return http
			.csrf(csrf -> csrf.disable())
			.sessionManagement(s -> s.sessionCreationPolicy(SessionCreationPolicy.STATELESS))
			.authorizeHttpRequests(auth -> auth
				// SSE는 비동기 디스패치로 응답을 이어 쓴다. 첫 요청에서 이미 인증했으므로 다시 막지 않는다.
				.dispatcherTypeMatchers(DispatcherType.ASYNC, DispatcherType.ERROR).permitAll()
				.requestMatchers(HttpMethod.POST, "/api/auth/login", "/api/auth/signup").permitAll()
				.requestMatchers("/api/admin/**").hasRole(Role.MANAGER.name())
				.requestMatchers(HttpMethod.GET, "/", "/index.html", "/swagger", "/swagger/**", "/specs/**",
						"/webjars/**", "/actuator/health/**", "/api/system/status")
				.permitAll()
				// business-mcp용 내부 API는 서비스 토큰으로 따로 연다. 구현 전까지는 막아 둔다.
				.requestMatchers("/internal/**").denyAll()
				.anyRequest().authenticated())
			.oauth2ResourceServer(o -> o
				.jwt(jwt -> jwt.jwtAuthenticationConverter(roleConverter()))
				.authenticationEntryPoint((req, res, e) -> writeProblem(res, json, HttpStatus.UNAUTHORIZED,
						"UNAUTHORIZED", "로그인이 필요하다"))
				.accessDeniedHandler((req, res, e) -> writeProblem(res, json, HttpStatus.FORBIDDEN, "FORBIDDEN",
						"권한이 없다")))
			.exceptionHandling(e -> e
				.authenticationEntryPoint((req, res, ex) -> writeProblem(res, json, HttpStatus.UNAUTHORIZED,
						"UNAUTHORIZED", "로그인이 필요하다"))
				.accessDeniedHandler((req, res, ex) -> writeProblem(res, json, HttpStatus.FORBIDDEN, "FORBIDDEN",
						"권한이 없다")))
			.build();
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

	private static void writeProblem(HttpServletResponse res, JsonMapper json, HttpStatus status, String code,
			String detail) throws java.io.IOException {
		ProblemDetail problem = ProblemDetail.forStatusAndDetail(status, detail);
		problem.setProperty("code", code);
		res.setStatus(status.value());
		res.setContentType(MediaType.APPLICATION_PROBLEM_JSON_VALUE);
		res.setCharacterEncoding(StandardCharsets.UTF_8.name());
		json.writeValue(res.getOutputStream(), problem);
	}
}
