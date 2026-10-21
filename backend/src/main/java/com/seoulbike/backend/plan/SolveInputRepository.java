package com.seoulbike.backend.plan;

import java.time.Instant;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.Collection;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.UUID;
import java.util.stream.Collectors;

import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Repository;
import tools.jackson.core.type.TypeReference;
import tools.jackson.databind.json.JsonMapper;

/** 경로 계산 입력을 만들 때 읽는 표: 구역 차고지·트럭(ref), 대여소 좌표(ref), 근무조 제약(V5), 요청한 작업. 읽기만 한다. */
@Repository
class SolveInputRepository {

	private static final TypeReference<List<String>> STRINGS = new TypeReference<>() {
	};

	record TruckRef(String truckId, int capacity) {
	}

	/** 승인된 제약 한 행 (계약 internal-api Constraint 중 계산에 쓰는 필드). */
	record ConstraintRow(String type, List<String> stationIds, Integer value) {
	}

	/** @param traceId 작업을 접수할 때 매긴 trace. 없으면 null */
	record TaskRef(String traceId) {
	}

	private final JdbcClient jdbc;
	private final JsonMapper json;

	SolveInputRepository(JdbcClient jdbc, JsonMapper json) {
		this.jdbc = jdbc;
		this.json = json;
	}

	Optional<SolveInput.Point> depot(String regionId) {
		return jdbc.sql("select depot_lat, depot_lon from ref.regions where region_id = :r")
			.param("r", regionId)
			.query((rs, i) -> new SolveInput.Point(rs.getDouble("depot_lat"), rs.getDouble("depot_lon")))
			.optional();
	}

	/** 구역의 운영 중인 트럭, 트럭 ID 순. */
	List<TruckRef> activeTrucks(String regionId) {
		return jdbc.sql("select truck_id, capacity from ref.trucks where region_id = :r and active order by truck_id")
			.param("r", regionId)
			.query((rs, i) -> new TruckRef(rs.getString("truck_id"), rs.getInt("capacity")))
			.list();
	}

	/** 대여소 좌표 (운영 여부와 상관없이). 지난 계획의 정류장이 지금은 운영하지 않을 수 있다. */
	Map<String, SolveInput.Point> positions(Collection<String> stationIds) {
		if (stationIds.isEmpty()) {
			return Map.of();
		}
		return jdbc.sql("select station_id, lat, lon from ref.stations where station_id in (:ids)")
			.param("ids", stationIds)
			.query((rs, i) -> Map.entry(rs.getString("station_id"),
					new SolveInput.Point(rs.getDouble("lat"), rs.getDouble("lon"))))
			.list()
			.stream()
			.collect(Collectors.toMap(Map.Entry::getKey, Map.Entry::getValue));
	}

	/** 이 근무조의 제약 중 [from, to)와 겹치는 것. 최근에 시작한 것부터 (같은 대여소에 재고 보정이 둘이면 앞의 것을 쓴다). */
	List<ConstraintRow> constraints(String shiftId, Instant from, Instant to) {
		return jdbc.sql("""
				select type, station_ids::text as station_ids, value from constraints
				where shift_id = :shiftId and valid_from < :to and valid_to > :from
				order by valid_from desc, created_at desc
				""")
			.param("shiftId", shiftId)
			.param("from", OffsetDateTime.ofInstant(from, ZoneOffset.UTC))
			.param("to", OffsetDateTime.ofInstant(to, ZoneOffset.UTC))
			.query((rs, i) -> new ConstraintRow(rs.getString("type"), json.readValue(rs.getString("station_ids"), STRINGS),
					rs.getObject("value", Integer.class)))
			.list();
	}

	Optional<TaskRef> task(UUID taskId) {
		return jdbc.sql("select trace_id from ai_tasks where task_id = :id")
			.param("id", taskId)
			.query((rs, i) -> new TaskRef(rs.getString("trace_id")))
			.optional();
	}
}
