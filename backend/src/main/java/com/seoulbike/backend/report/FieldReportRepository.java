package com.seoulbike.backend.report;

import java.time.Instant;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.UUID;

import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Repository;

@Repository
class FieldReportRepository {

	enum Issue {
		BLOCKED, CONSTRUCTION, INVENTORY_MISMATCH, OTHER
	}

	record NewReport(UUID reportId, String shiftId, String truckId, String stationId, int actualBikes, Integer loaded,
			Integer unloaded, Issue issue, String memo, String reportedBy, Instant reportedAt) {
	}

	private final JdbcClient jdbc;

	FieldReportRepository(JdbcClient jdbc) {
		this.jdbc = jdbc;
	}

	void insert(NewReport r) {
		jdbc.sql("""
				insert into field_reports (report_id, shift_id, truck_id, station_id, actual_bikes, loaded, unloaded,
				                           issue, memo, reported_by, reported_at)
				values (:reportId, :shiftId, :truckId, :stationId, :actualBikes, :loaded, :unloaded,
				        :issue, :memo, :reportedBy, :reportedAt)
				""")
			.param("reportId", r.reportId())
			.param("shiftId", r.shiftId())
			.param("truckId", r.truckId())
			.param("stationId", r.stationId())
			.param("actualBikes", r.actualBikes())
			.param("loaded", r.loaded())
			.param("unloaded", r.unloaded())
			.param("issue", r.issue() == null ? null : r.issue().name())
			.param("memo", r.memo())
			.param("reportedBy", r.reportedBy())
			.param("reportedAt", OffsetDateTime.ofInstant(r.reportedAt(), ZoneOffset.UTC))
			.update();
	}

	void linkTask(UUID reportId, UUID taskId) {
		jdbc.sql("update field_reports set task_id = :taskId where report_id = :reportId")
			.param("taskId", taskId)
			.param("reportId", reportId)
			.update();
	}

	/** 재경로 작업을 큐에 넣지 못했을 때 보고도 지운다. 기사가 다시 보내면 중복 없이 새로 남는다. */
	void delete(UUID reportId) {
		jdbc.sql("delete from field_reports where report_id = :reportId").param("reportId", reportId).update();
	}
}
