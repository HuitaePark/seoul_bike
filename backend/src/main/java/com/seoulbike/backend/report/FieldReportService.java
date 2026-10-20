package com.seoulbike.backend.report;

import java.time.Instant;
import java.util.UUID;

import org.springframework.stereotype.Service;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ObjectNode;

import com.seoulbike.backend.auth.CurrentUser;
import com.seoulbike.backend.common.ApiException;
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
	private final JsonMapper json;

	FieldReportService(FieldReportRepository reports, TaskService tasks, JsonMapper json) {
		this.reports = reports;
		this.tasks = tasks;
		this.json = json;
	}

	Accepted report(CurrentUser driver, FieldReportController.FieldReportRequest req) {
		var report = new NewReport(UUID.randomUUID(), req.shiftId(), driver.truckId(), req.stationId(), req.actualBikes(),
				req.loaded(), req.unloaded(), req.issue(), req.memo(), driver.id(), Instant.now());
		reports.insert(report);
		if (report.issue() == null) {
			return new Accepted(report.reportId(), null);
		}
		UUID taskId;
		try {
			taskId = tasks.startFieldReport(driver, report.shiftId(), contract(report));
		}
		catch (ApiException e) {
			reports.delete(report.reportId());
			throw e;
		}
		reports.linkTask(report.reportId(), taskId);
		return new Accepted(report.reportId(), taskId);
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
