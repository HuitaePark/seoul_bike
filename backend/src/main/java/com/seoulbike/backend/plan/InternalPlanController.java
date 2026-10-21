package com.seoulbike.backend.plan;

import java.util.List;
import java.util.UUID;

import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Size;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RestController;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.node.ObjectNode;

/**
 * business-mcp `get_current_plan`·`request_route_plan`·`submit_plan`·`propose_route_change` (internal-api).
 * 서비스 토큰으로만 연다 (SecurityConfig).
 */
@RestController
class InternalPlanController {

	/** 계약 ApprovedChange. */
	record ApprovedChange(@NotNull UUID approvalId, @NotNull UUID taskId) {
	}

	/** 계약 ApprovedChange + candidateId. */
	record RouteApplyRequest(@NotNull UUID approvalId, @NotNull UUID taskId, @NotBlank @Size(max = 64) String candidateId) {
	}

	/** 계약 SolveRequest. */
	record SolveRequest(@NotBlank @Size(max = 100) String shiftId, @NotNull SolveGateway.Kind kind,
			@Size(max = 50) List<@NotBlank String> truckIds, @Size(max = 200) List<@NotBlank String> excludeStationIds,
			@Valid RequestedBy requestedBy) {
	}

	record RequestedBy(@NotNull UUID taskId) {
	}

	private final PlanService plans;
	private final PlanSolver solver;

	InternalPlanController(PlanService plans, PlanSolver solver) {
		this.plans = plans;
		this.solver = solver;
	}

	/** 트랜잭션 밖에서 결과를 최대 app.solve.timeout 기다린다. */
	@PostMapping("/internal/plans/solve")
	ObjectNode solve(@Valid @RequestBody SolveRequest req) {
		return solver.solve(new PlanSolver.Command(req.shiftId(), req.kind(), req.truckIds(), req.excludeStationIds(),
				req.requestedBy() == null ? null : req.requestedBy().taskId()));
	}

	@GetMapping("/internal/shifts/{shiftId}/plan")
	ObjectNode current(@PathVariable String shiftId) {
		return plans.current(shiftId);
	}

	@PostMapping("/internal/plans/{planId}/activate")
	ObjectNode activate(@PathVariable String planId, @Valid @RequestBody ApprovedChange req) {
		return plans.activate(planId, req.approvalId(), req.taskId());
	}

	@PostMapping("/internal/routes/{truckId}/apply")
	JsonNode apply(@PathVariable String truckId, @Valid @RequestBody RouteApplyRequest req) {
		return plans.apply(truckId, req.approvalId(), req.taskId(), req.candidateId());
	}
}
