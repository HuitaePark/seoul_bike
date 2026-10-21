package com.seoulbike.backend.report;

import java.time.Instant;
import java.util.UUID;

import org.springframework.stereotype.Service;
import org.springframework.transaction.support.TransactionTemplate;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ObjectNode;

import com.seoulbike.backend.audit.AuditLog;
import com.seoulbike.backend.auth.CurrentUser;
import com.seoulbike.backend.common.SeoulTime;
import com.seoulbike.backend.plan.RouteProgress;
import com.seoulbike.backend.report.FieldReportRepository.Issue;
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
	private final TransactionTemplate tx;
	private final RouteProgress progress;

	FieldReportService(FieldReportRepository reports, TaskService tasks, AuditLog audit, JsonMapper json,
			TransactionTemplate tx, RouteProgress progress) {
		this.reports = reports;
		this.tasks = tasks;
		this.audit = audit;
		this.json = json;
		this.tx = tx;
		this.progress = progress;
	}

	/**
	 * 감사 기록은 보고가 확정되는 마지막 단계와 같은 트랜잭션에 남긴다.
	 * issue가 없으면 저장과 함께, 있으면 재경로 작업을 큐에 넣은 뒤 보고에 작업을 잇는 단계와 함께.
	 * 정류장 진행(RouteProgress)은 저장과 같은 트랜잭션에서 재경로 작업보다 먼저 남긴다: agent가 트럭 상태를 읽을 때 이 정류장이
	 * 이미 끝나 있어야 한다. 큐에 넣지 못해 보고를 지워도 진행은 남는다 (기사는 그 정류장에 갔다). 다시 보내면 남은 정류장이 아니라
	 * 진행은 그대로다.
	 */
	Accepted report(CurrentUser driver, FieldReportController.FieldReportRequest req) {
		var report = new NewReport(UUID.randomUUID(), req.shiftId(), driver.truckId(), req.stationId(), req.actualBikes(),
				req.loaded(), req.unloaded(), req.issue(), req.memo(), driver.id(), Instant.now());
		if (report.issue() == null) {
			tx.executeWithoutResult(s -> {
				reports.insert(report);
				recordProgress(report);
				audit(driver, report, null);
			});
			return new Accepted(report.reportId(), null);
		}
		tx.executeWithoutResult(s -> {
			reports.insert(report);
			recordProgress(report);
		});
		UUID taskId;
		try {
			taskId = tasks.startFieldReport(driver, report.shiftId(), contract(report));
		}
		catch (RuntimeException e) {
			// 큐 실패(503)든 DB 오류든 작업 없는 issue 보고를 남기지 않는다. 기사가 다시 보내면 새로 남는다.
			reports.delete(report.reportId());
			throw e;
		}
		tx.executeWithoutResult(s -> {
			reports.linkTask(report.reportId(), taskId);
			audit(driver, report, taskId);
		});
		return new Accepted(report.reportId(), taskId);
	}

	/** 진입 불가·공사는 그 정류장을 처리하지 못한 것이다 (SKIPPED). 그 밖의 보고는 도착해서 처리했다 (DONE). */
	private void recordProgress(NewReport r) {
		boolean served = r.issue() != Issue.BLOCKED && r.issue() != Issue.CONSTRUCTION;
		progress.record(r.shiftId(), r.truckId(), r.stationId(), served, r.loaded(), r.unloaded());
	}

	/** 메모는 개인정보가 섞일 수 있어 감사 기록에 남기지 않는다. */
	private void audit(CurrentUser driver, NewReport report, UUID taskId) {
		audit.record(report.shiftId(), AuditLog.Type.FIELD_REPORT, AuditLog.Actor.of(driver), report.reportId().toString(),
				taskId, report.stationId() + " 실제 재고 " + report.actualBikes() + "대"
						+ (report.issue() == null ? "" : " · " + report.issue() + " → 재경로 작업"));
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
			.put("reportedAt", SeoulTime.format(r.reportedAt()));
	}
}
