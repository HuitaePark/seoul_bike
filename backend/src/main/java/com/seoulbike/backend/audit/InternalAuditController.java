package com.seoulbike.backend.audit;

import java.time.Instant;
import java.util.List;

import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

/** business-mcp `get_audit` (internal-api, ISS-06). 서비스 토큰으로만 연다. */
@RestController
class InternalAuditController {

	private final AuditLog audit;

	InternalAuditController(AuditLog audit) {
		this.audit = audit;
	}

	@GetMapping("/internal/shifts/{shiftId}/audit")
	List<AuditLog.AuditEvent> audit(@PathVariable String shiftId, @RequestParam(required = false) Instant since) {
		return audit.forShift(shiftId, since);
	}
}
