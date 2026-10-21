package com.seoulbike.backend.agentlog;

import java.sql.ResultSet;
import java.sql.SQLException;
import java.time.Duration;
import java.time.Instant;
import java.time.OffsetDateTime;
import java.time.format.DateTimeParseException;
import java.util.ArrayList;
import java.util.Collections;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.UUID;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Transactional;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

/**
 * ai.traces 메시지를 agent_trace_events에 저장하고 관리자 로그로 읽는다. 같은 eventId는 한 번만 저장한다.
 *
 * <p>id는 받은 순서다. backend가 둘 떠 있어도(무중단 배포 중) id 순서와 커밋 순서가 같도록 저장을 트랜잭션 잠금 하나로
 * 줄 세운다. 그래서 실시간 스트림이 "마지막으로 보낸 id 다음"을 읽으면 기록을 건너뛰지 않는다. 저장은 한 건이 작아 잠금이 짧다.
 */
@Component
class AgentTraceStore {

	private static final Logger log = LoggerFactory.getLogger(AgentTraceStore.class);

	static final int MAX_LIMIT = 500;
	/** pg_advisory_xact_lock 키. 다른 기능의 잠금과 겹치지 않는 임의의 수. */
	private static final long INSERT_LOCK = 7_302_101_022L;

	private static final String COLUMNS = """
			id, event_id, task_id, shift_id, trace_id, run_id, source, kind, turn, title, detail::text as detail,
			occurred_at, received_at""";
	private static final String FILTER = """
			(cast(:taskId as uuid) is null or task_id = cast(:taskId as uuid))
			and (cast(:shiftId as varchar) is null or shift_id = :shiftId)""";

	record Filter(UUID taskId, String shiftId) {

		static final Filter ALL = new Filter(null, null);

		boolean matches(AgentTrace trace) {
			return (taskId == null || taskId.equals(trace.taskId())) && (shiftId == null || shiftId.equals(trace.shiftId()));
		}
	}

	record Page(List<AgentTrace> items, boolean hasMore) {
	}

	private final JdbcClient jdbc;
	private final JsonMapper json;
	private final Duration retention;

	AgentTraceStore(JdbcClient jdbc, JsonMapper json, @Value("${app.agent-trace.retention}") Duration retention) {
		this.jdbc = jdbc;
		this.json = json;
		this.retention = retention;
	}

	/**
	 * 형식이 맞지 않으면 IllegalArgumentException (consumer가 건너뛴다). 이미 받은 eventId면 빈 값.
	 * 길이 제한이 있는 값은 계약 한도로 자르고, 모르는 kind·source도 그대로 저장한다 (agent가 종류를 늘려도 기록이 남는다).
	 */
	@Transactional
	Optional<AgentTrace> insert(JsonNode message) {
		if (!message.isObject()) {
			throw new IllegalArgumentException("ai.traces 메시지는 JSON 객체여야 한다");
		}
		UUID eventId = UUID.fromString(message.path("eventId").asString());
		UUID taskId = UUID.fromString(message.path("taskId").asString());
		String shiftId = required(message, "shiftId", 100);
		String source = required(message, "source", 16);
		String kind = required(message, "kind", 24);
		String title = clip(message.path("title").asString(""), 300);
		JsonNode detail = message.path("detail").isObject() ? message.get("detail") : json.createObjectNode();
		JsonNode turn = message.path("turn");

		jdbc.sql("select pg_advisory_xact_lock(:key)").param("key", INSERT_LOCK).query((rs, i) -> 0).single();
		return jdbc.sql("""
				insert into agent_trace_events (event_id, task_id, shift_id, trace_id, run_id, source, kind, turn, title,
				                                detail, occurred_at)
				values (:eventId, :taskId, :shiftId, :traceId, :runId, :source, :kind, :turn, :title, cast(:detail as jsonb),
				        :occurredAt)
				on conflict (event_id) do nothing
				returning""" + " " + COLUMNS)
			.param("eventId", eventId)
			.param("taskId", taskId)
			.param("shiftId", shiftId)
			.param("traceId", optional(message, "traceId", 64))
			.param("runId", optional(message, "runId", 64))
			.param("source", source)
			.param("kind", kind)
			.param("turn", turn.isInt() ? turn.asInt() : null)
			.param("title", title)
			.param("detail", json.writeValueAsString(detail))
			.param("occurredAt", occurredAt(message.path("occurredAt").asString("")))
			.query(this::map)
			.optional();
	}

	/** 가장 최근 limit건 (오래된 것부터). hasMore: 그보다 앞선 기록이 있다. */
	Page latest(Filter filter, int limit) {
		return olderFirst(query("where " + FILTER + " order by id desc limit :limit", filter, Map.of(), limit + 1), limit);
	}

	/** before보다 앞선 limit건 (오래된 것부터). hasMore: 그보다 더 앞선 기록이 있다. */
	Page before(Filter filter, long before, int limit) {
		return olderFirst(query("where id < :before and " + FILTER + " order by id desc limit :limit", filter,
				Map.of("before", before), limit + 1), limit);
	}

	/** after 다음 기록을 limit건까지 (오래된 것부터). hasMore: 더 뒤의 기록이 있다. 실시간 스트림도 이것으로 읽는다. */
	Page after(Filter filter, long after, int limit) {
		List<AgentTrace> rows = query("where id > :after and " + FILTER + " order by id limit :limit", filter,
				Map.of("after", after), limit + 1);
		return new Page(rows.subList(0, Math.min(limit, rows.size())), rows.size() > limit);
	}

	/** 가장 마지막 id. 기록이 없으면 0. Last-Event-ID 없이 붙은 스트림이 여기부터 따라간다. */
	long lastId() {
		return jdbc.sql("select coalesce(max(id), 0) from agent_trace_events").query(Long.class).single();
	}

	/** 보관 기간이 지난 기록을 지운다 (매시 17분). 관리자 로그는 감사 기록이 아니라 오래 두지 않는다. */
	@Scheduled(cron = "0 17 * * * *")
	void purge() {
		int deleted = jdbc.sql("delete from agent_trace_events where received_at < :cutoff")
			.param("cutoff", OffsetDateTime.now().minus(retention))
			.update();
		if (deleted > 0) {
			log.info("보관 기간({})이 지난 추론 기록 {}건을 지웠다", retention, deleted);
		}
	}

	private List<AgentTrace> query(String where, Filter filter, Map<String, Object> extra, int limit) {
		Map<String, Object> params = new HashMap<>(extra);
		params.put("taskId", filter.taskId());
		params.put("shiftId", filter.shiftId());
		params.put("limit", limit);
		return jdbc.sql("select " + COLUMNS + " from agent_trace_events " + where).params(params).query(this::map).list();
	}

	private static Page olderFirst(List<AgentTrace> newestFirst, int limit) {
		List<AgentTrace> page = new ArrayList<>(newestFirst.subList(0, Math.min(limit, newestFirst.size())));
		Collections.reverse(page);
		return new Page(page, newestFirst.size() > limit);
	}

	private AgentTrace map(ResultSet rs, int row) throws SQLException {
		Object turn = rs.getObject("turn");
		return new AgentTrace(rs.getLong("id"), rs.getObject("event_id", UUID.class), rs.getObject("task_id", UUID.class),
				rs.getString("shift_id"), rs.getString("trace_id"), rs.getString("run_id"), rs.getString("source"),
				rs.getString("kind"), turn == null ? null : ((Number) turn).intValue(), rs.getString("title"),
				json.readTree(rs.getString("detail")), instant(rs, "occurred_at"), instant(rs, "received_at"));
	}

	private static Instant instant(ResultSet rs, String column) throws SQLException {
		OffsetDateTime value = rs.getObject(column, OffsetDateTime.class);
		return value == null ? null : value.toInstant();
	}

	private static String required(JsonNode message, String field, int max) {
		String value = message.path(field).asString("");
		if (value.isBlank() || value.length() > max) {
			throw new IllegalArgumentException("ai.traces " + field + "가 없거나 " + max + "자를 넘는다");
		}
		return value;
	}

	private static String optional(JsonNode message, String field, int max) {
		String value = message.path(field).asString(null);
		return value == null || value.isBlank() ? null : clip(value, max);
	}

	private static String clip(String value, int max) {
		return value.length() <= max ? value : value.substring(0, max);
	}

	/** agent가 보낸 시각. 읽을 수 없으면 받은 시각으로 둔다 (기록을 버리지 않는다). */
	private static OffsetDateTime occurredAt(String value) {
		try {
			return OffsetDateTime.parse(value);
		}
		catch (DateTimeParseException e) {
			return OffsetDateTime.now();
		}
	}
}
