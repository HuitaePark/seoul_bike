package com.seoulbike.backend.report;

import java.time.Instant;
import java.util.UUID;

import org.springframework.stereotype.Service;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ObjectNode;

import com.seoulbike.backend.audit.AuditLog;
import com.seoulbike.backend.auth.CurrentUser;
import com.seoulbike.backend.report.FieldReportRepository.NewReport;
import com.seoulbike.backend.task.TaskService;

/**
 * 현장 보고 (계약 openapi POST /api/reports). 트럭은 보고한 기사의 트럭이다.
 * issue가 있으면 재경로 FIELD_REPORT 작업을 만들고, 보고 값(계약 FieldReport)을 명령에 실어 보낸다.
 */
@Service
class FieldReportService {

	record Accepted(UUID reportId, UUID taskId) {
	}

	private final FieldReportRepository reports;
	private final TaskService tasks;
	private final AuditLog audit;
	private final JsonMapper json;

	FieldReportService(FieldReportRepository reports, TaskService tasks, AuditLog audit, JsonMapper json) {
		this.reports = reports;
		this.tasks = tasks;
		this.audit = audit;
		this.json = json;
	}

	Accepted report(CurrentUser driver, FieldReportController.FieldReportRequest req) {
		var report = new NewReport(UUID.randomUUID(), req.shiftId(), driver.truckId(), req.stationId(), req.actualBikes(),
				req.loaded(), req.unloaded(), req.issue(), req.memo(), driver.id(), Instant.now());
		reports.insert(report);
		UUID taskId = report.issue() == null ? null : startReroute(driver, report);
		// 메모는 개인정보가 섞일 수 있어 감사 기록에 남기지 않는다.
		audit.record(report.shiftId(), AuditLog.Type.FIELD_REPORT, AuditLog.Actor.of(driver), report.reportId().toString(),
				taskId, report.stationId() + " 실제 재고 " + report.actualBikes() + "대"
						+ (report.issue() == null ? "" : " · " + report.issue() + " → 재경로 작업"));
		return new Accepted(report.reportId(), taskId);
	}

	private UUID startReroute(CurrentUser driver, NewReport report) {
		UUID taskId;
		try {
			taskId = tasks.startFieldReport(driver, report.shiftId(), contract(report));
		}
		catch (RuntimeException e) {
			// 큐 실패(503)든 DB 오류든 작업 없는 issue 보고를 남기지 않는다. 기사가 다시 보내면 새로 남는다.
			reports.delete(report.reportId());
			throw e;
		}
		reports.linkTask(report.reportId(), taskId);
		return taskId;
	}

	private ObjectNode contract(NewReport r) {
		return json.createObjectNode()
			.put("reportId", r.reportId().toString())
			.put("truckId", r.truckId())
			.put("stationId", r.stationId())
			.put("actualBikes", r.actualBikes())
			.put("loaded", r.loaded())
			.put("unloaded", r.unloaded())
			.put("issue", r.issue().name())
			.put("memo", r.memo())
			.put("reportedAt", r.reportedAt().toString());
	}
}
