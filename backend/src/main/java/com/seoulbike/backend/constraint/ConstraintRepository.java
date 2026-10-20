package com.seoulbike.backend.constraint;

import java.time.OffsetDateTime;
import java.util.List;
import java.util.UUID;

import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Repository;
import tools.jackson.core.type.TypeReference;
import tools.jackson.databind.json.JsonMapper;

import com.seoulbike.backend.constraint.ConstraintController.Constraint;
import com.seoulbike.backend.constraint.ConstraintController.ConstraintRequest;
import com.seoulbike.backend.constraint.ConstraintController.ConstraintType;

@Repository
class ConstraintRepository {

	private static final TypeReference<List<String>> STRINGS = new TypeReference<>() {
	};

	private final JdbcClient jdbc;
	private final JsonMapper json;

	ConstraintRepository(JdbcClient jdbc, JsonMapper json) {
		this.jdbc = jdbc;
		this.json = json;
	}

	/** 같은 approval_id가 이미 있으면 넣지 않는다 (재시도). */
	void insertIfAbsent(UUID constraintId, ConstraintRequest req) {
		jdbc.sql("""
				insert into constraints (constraint_id, approval_id, task_id, shift_id, type, station_ids, value,
				                         valid_from, valid_to, reason)
				values (:constraintId, :approvalId, :taskId, :shiftId, :type, cast(:stationIds as jsonb), :value,
				        :validFrom, :validTo, :reason)
				on conflict (approval_id) do nothing
				""")
			.param("constraintId", constraintId)
			.param("approvalId", req.approvalId())
			.param("taskId", req.taskId())
			.param("shiftId", req.shiftId())
			.param("type", req.type().name())
			.param("stationIds", json.writeValueAsString(req.stationIds()))
			.param("value", req.value())
			.param("validFrom", req.validFrom())
			.param("validTo", req.validTo())
			.param("reason", req.reason())
			.update();
	}

	Constraint findByApproval(UUID approvalId) {
		return jdbc.sql("""
				select constraint_id, approval_id, task_id, shift_id, type, station_ids::text as station_ids, value,
				       valid_from, valid_to, reason
				from constraints where approval_id = :approvalId
				""")
			.param("approvalId", approvalId)
			.query((rs, i) -> new Constraint(rs.getString("constraint_id"), rs.getObject("approval_id", UUID.class),
					rs.getObject("task_id", UUID.class), rs.getString("shift_id"), ConstraintType.valueOf(rs.getString("type")),
					json.readValue(rs.getString("station_ids"), STRINGS), rs.getObject("value", Integer.class),
					rs.getObject("valid_from", OffsetDateTime.class), rs.getObject("valid_to", OffsetDateTime.class),
					rs.getString("reason")))
			.single();
	}
}
