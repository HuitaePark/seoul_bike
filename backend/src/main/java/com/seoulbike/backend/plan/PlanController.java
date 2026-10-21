package com.seoulbike.backend.plan;

import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RestController;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.node.ObjectNode;

import com.seoulbike.backend.auth.CurrentUser;

/** 화면용 계획 조회. 기사 경로는 기사(DRIVER)만 부를 수 있고, 트럭은 토큰에서 꺼낸다 (다른 트럭 경로를 볼 수 없다). */
@RestController
class PlanController {

	private final PlanService plans;

	PlanController(PlanService plans) {
		this.plans = plans;
	}

	@GetMapping("/api/shifts/{shiftId}/plan")
	ObjectNode shiftPlan(@PathVariable String shiftId) {
		return plans.latest(shiftId);
	}

	@GetMapping("/api/drivers/me/route")
	JsonNode myRoute(@AuthenticationPrincipal Jwt jwt) {
		return plans.driverRoute(CurrentUser.from(jwt).truckId());
	}
}
