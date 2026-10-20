package com.seoulbike.backend.task;

import java.sql.ResultSet;
import java.sql.SQLException;
import java.time.Instant;
import java.time.OffsetDateTime;
import java.util.HashMap;
import java.util.List;
import java.util.Optional;
import java.util.UUID;

import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Repository;
import tools.jackson.databind.json.JsonMapper;

import com.seoulbike.backend.auth.CurrentUser;
import com.seoulbike.backend.auth.Role;

@Repository
class TaskRepository {

	/** 화면에 보이는 작업: 관리자·책임자는 전부, 기사는 자기가 만든 것과 자기 트럭 승인이 걸린 것. */
	private static final String VISIBLE = """
			(:seeAll or t.created_by = :userId
			  or exists (select 1 from approvals a where a.task_id = t.task_id and a.truck_id = :truckId))
			""";

	private static final String SELECT = """
			select t.task_id, t.type, t.status, t.shift_id, t.created_by, t.created_at, t.updated_at,
			       t.answer, t.sources::text as sources, t.error, t.trace_id,
			       a.request::text as pending_approval
			from ai_tasks t
			left join approvals a on a.approval_id = t.pending_approval_id and a.status = 'PENDING'
			""";

	record Page(List<Task> items, int page, int size, long total) {
	}

	private final JdbcClient jdbc;
	private final JsonMapper json;

	TaskRepository(JdbcClient jdbc, JsonMapper json) {
		this.jdbc = jdbc;
		this.json = json;
	}

	void insert(UUID taskId, TaskType type, String shiftId, String text, String createdBy, String traceId) {
		jdbc.sql("""
				insert into ai_tasks (task_id, type, status, shift_id, input_text, created_by, trace_id)
				values (:taskId, :type, 'QUEUED', :shiftId, :text, :createdBy, :traceId)
				""")
			.param("taskId", taskId)
			.param("type", type.name())
			.param("shiftId", shiftId)
			.param("text", text)
			.param("createdBy", createdBy)
			.param("traceId", traceId)
			.update();
	}

	Optional<Task> findVisible(UUID taskId, CurrentUser user) {
		return jdbc.sql(SELECT + " where t.task_id = :taskId and " + VISIBLE)
			.param("taskId", taskId)
			.param("seeAll", seesAll(user))
			.param("userId", user.id())
			.param("truckId", user.truckId())
			.query(this::map)
			.optional();
	}

	Optional<Task> find(UUID taskId) {
		return jdbc.sql(SELECT + " where t.task_id = :taskId").param("taskId", taskId).query(this::map).optional();
	}

	Page list(CurrentUser user, boolean mine, TaskStatus status, String shiftId, int page, int size) {
		String where = " where " + VISIBLE + """
				 and (not :mine or t.created_by = :userId)
				 and (cast(:status as varchar) is null or t.status = :status)
				 and (cast(:shiftId as varchar) is null or t.shift_id = :shiftId)
				""";
		var params = new HashMap<String, Object>();
		params.put("seeAll", seesAll(user));
		params.put("userId", user.id());
		params.put("truckId", user.truckId());
		params.put("mine", mine);
		params.put("status", status == null ? null : status.name());
		params.put("shiftId", shiftId);
		long total = jdbc.sql("select count(*) from ai_tasks t" + where).params(params).query(Long.class).single();
		params.put("limit", size);
		params.put("offset", (long) page * size);
		List<Task> items = jdbc.sql(SELECT + where + " order by t.created_at desc limit :limit offset :offset")
			.params(params)
			.query(this::map)
			.list();
		return new Page(items, page, size, total);
	}

	/** 처음 진행 이벤트가 오면 QUEUED → RUNNING. 다른 상태는 건드리지 않는다. */
	void markRunning(UUID taskId) {
		jdbc.sql("update ai_tasks set status = 'RUNNING', updated_at = now() where task_id = :taskId and status = 'QUEUED'")
			.param("taskId", taskId)
			.update();
	}

	void waitForApproval(UUID taskId, UUID approvalId) {
		jdbc.sql("""
				update ai_tasks set status = 'WAITING_APPROVAL', pending_approval_id = :approvalId, updated_at = now()
				where task_id = :taskId
				""")
			.param("approvalId", approvalId)
			.param("taskId", taskId)
			.update();
	}

	void resume(UUID taskId) {
		jdbc.sql("""
				update ai_tasks set status = 'RUNNING', pending_approval_id = null, updated_at = now()
				where task_id = :taskId
				""")
			.param("taskId", taskId)
			.update();
	}

	void finish(UUID taskId, String answer, String sourcesJson) {
		jdbc.sql("""
				update ai_tasks set status = 'DONE', answer = :answer, sources = cast(:sources as jsonb),
				       pending_approval_id = null, updated_at = now()
				where task_id = :taskId
				""")
			.param("answer", answer)
			.param("sources", sourcesJson)
			.param("taskId", taskId)
			.update();
	}

	void fail(UUID taskId, String error) {
		jdbc.sql("""
				update ai_tasks set status = 'FAILED', error = :error, pending_approval_id = null, updated_at = now()
				where task_id = :taskId
				""")
			.param("error", error)
			.param("taskId", taskId)
			.update();
	}

	private static boolean seesAll(CurrentUser user) {
		return user.role() != Role.DRIVER;
	}

	private Task map(ResultSet rs, int row) throws SQLException {
		String pending = rs.getString("pending_approval");
		return new Task(rs.getObject("task_id", UUID.class), TaskType.valueOf(rs.getString("type")),
				TaskStatus.valueOf(rs.getString("status")), rs.getString("shift_id"), rs.getString("created_by"),
				instant(rs, "created_at"), instant(rs, "updated_at"), rs.getString("answer"),
				json.readTree(rs.getString("sources")), pending == null ? null : json.readTree(pending),
				rs.getString("error"), rs.getString("trace_id"));
	}

	static Instant instant(ResultSet rs, String column) throws SQLException {
		OffsetDateTime value = rs.getObject(column, OffsetDateTime.class);
		return value == null ? null : value.toInstant();
	}
}
