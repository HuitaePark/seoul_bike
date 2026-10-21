package com.seoulbike.backend.report;

import java.sql.ResultSet;
import java.sql.SQLException;
import java.time.Instant;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.List;
import java.util.UUID;

import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Repository;

/** 현장 보고 표. 대여소 상세(station 패키지)도 최근 보고를 읽으므로 public이다. */
@Repository
public class FieldReportRepository {

	enum Issue {
		BLOCKED, CONSTRUCTION, INVENTORY_MISMATCH, OTHER
	}

	record NewReport(UUID reportId, String shiftId, String truckId, String stationId, int actualBikes, Integer loaded,
			Integer unloaded, Issue issue, String memo, String reportedBy, Instant reportedAt) {
	}

	/** 계약 internal-api FieldReport. driver는 이름 대신 "{트럭} 기사"로 가린다 (agent에 개인정보를 보내지 않는다). */
	public record ShiftReport(String reportId, String truckId, String driver, String stationId, int actualBikes,
			Integer loaded, Integer unloaded, String issue, String memo, Instant reportedAt) {
	}

	private static final String REPORT_COLUMNS =
			"report_id, truck_id, station_id, actual_bikes, loaded, unloaded, issue, memo, reported_at";

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

	List<ShiftReport> forShift(String shiftId, Instant since) {
		return jdbc.sql("select " + REPORT_COLUMNS + """

				from field_reports
				where shift_id = :shiftId and (cast(:since as timestamptz) is null or reported_at > :since)
				order by reported_at
				""")
			.param("shiftId", shiftId)
			.param("since", since == null ? null : OffsetDateTime.ofInstant(since, ZoneOffset.UTC))
			.query(FieldReportRepository::report)
			.list();
	}

	/** 대여소 상세의 최근 보고 (최근 것부터 limit건, 근무조 상관없이). 현장에서 본 재고·사정이 실시간 재고와 다를 때 근거가 된다. */
	public List<ShiftReport> recentAtStation(String stationId, int limit) {
		return jdbc.sql("select " + REPORT_COLUMNS + """

				from field_reports
				where station_id = :stationId
				order by reported_at desc
				limit :limit
				""")
			.param("stationId", stationId)
			.param("limit", limit)
			.query(FieldReportRepository::report)
			.list();
	}

	private static ShiftReport report(ResultSet rs, int row) throws SQLException {
		return new ShiftReport(rs.getString("report_id"), rs.getString("truck_id"), rs.getString("truck_id") + " 기사",
				rs.getString("station_id"), rs.getInt("actual_bikes"), rs.getObject("loaded", Integer.class),
				rs.getObject("unloaded", Integer.class), rs.getString("issue"), rs.getString("memo"),
				rs.getObject("reported_at", OffsetDateTime.class).toInstant());
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
