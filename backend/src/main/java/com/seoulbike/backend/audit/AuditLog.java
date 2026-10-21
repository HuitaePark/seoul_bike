package com.seoulbike.backend.audit;

import java.time.Instant;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.List;
import java.util.UUID;

import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Component;

import com.seoulbike.backend.auth.CurrentUser;
import com.seoulbike.backend.auth.Role;

/**
 * 감사 기록 (FR-18, FR-08). append-only 표(V6)에 쓴다. 바뀐 일과 같은 트랜잭션에서 부르면 둘이 함께 남거나 함께 없어진다.
 * 사람 이름은 남기지 않는다. 표시는 "관리자", "TR-01 기사"처럼 역할로 가린다.
 */
@Component
public class AuditLog {

	public enum Type {
		APPROVAL_APPROVED, APPROVAL_REJECTED, APPROVAL_REOPENED, FIELD_REPORT, CONSTRAINT_ADDED, PLAN_ACTIVATED,
		ROUTE_CHANGED
	}

	/** 누가 했는가. role은 MANAGER·DRIVER·EXECUTIVE 또는 backend 스스로의 SYSTEM. userId는 내부 추적용이고 조회 결과에는 나가지 않는다. */
	public record Actor(String userId, String role, String display) {

		public static Actor of(CurrentUser user) {
			return new Actor(user.id(), user.role().name(), display(user.role(), user.truckId()));
		}

		/** 승인한 관리자. 변경을 실행한 것은 에이전트지만 결정한 사람은 그 관리자다. */
		public static Actor approvingManager(String userId) {
			return new Actor(userId, Role.MANAGER.name(), display(Role.MANAGER, null));
		}

		/** 경로 변경을 마지막으로 수락한 그 트럭 기사. 적용은 이 수락으로 실행된다 (FR-13). */
		public static Actor approvingDriver(String userId, String truckId) {
			return new Actor(userId, Role.DRIVER.name(), display(Role.DRIVER, truckId));
		}

		/** backend가 스스로 한 일 (예: 재개 명령을 못 보내 결정을 되돌림). */
		public static Actor system() {
			return new Actor(null, "SYSTEM", "시스템");
		}

		static String display(Role role, String truckId) {
			return switch (role) {
				case MANAGER -> "관리자";
				case DRIVER -> truckId + " 기사";
				case EXECUTIVE -> "책임자";
			};
		}
	}

	/** 계약 internal-api AuditEvent. */
	record AuditEvent(String auditId, Instant occurredAt, Type type, ActorView actor, String targetId, String detail) {
	}

	record ActorView(String role, String display) {
	}

	private final JdbcClient jdbc;

	AuditLog(JdbcClient jdbc) {
		this.jdbc = jdbc;
	}

	public void record(String shiftId, Type type, Actor actor, String targetId, UUID taskId, String detail) {
		jdbc.sql("""
				insert into audit_events (shift_id, type, actor_id, actor_role, actor_display, target_id, task_id, detail)
				values (:shiftId, :type, :actorId, :actorRole, :actorDisplay, :targetId, :taskId, :detail)
				""")
			.param("shiftId", shiftId)
			.param("type", type.name())
			.param("actorId", actor.userId())
			.param("actorRole", actor.role())
			.param("actorDisplay", actor.display())
			.param("targetId", targetId)
			.param("taskId", taskId)
			.param("detail", detail.length() > 500 ? detail.substring(0, 500) : detail)
			.update();
	}

	List<AuditEvent> forShift(String shiftId, Instant since) {
		return jdbc.sql("""
				select audit_id, occurred_at, type, actor_role, actor_display, target_id, detail
				from audit_events
				where shift_id = :shiftId and (cast(:since as timestamptz) is null or occurred_at > :since)
				order by occurred_at, audit_id
				""")
			.param("shiftId", shiftId)
			.param("since", since == null ? null : OffsetDateTime.ofInstant(since, ZoneOffset.UTC))
			.query((rs, i) -> new AuditEvent("AU-" + rs.getLong("audit_id"),
					rs.getObject("occurred_at", OffsetDateTime.class).toInstant(), Type.valueOf(rs.getString("type")),
					new ActorView(rs.getString("actor_role"), rs.getString("actor_display")),
					rs.getString("target_id"), rs.getString("detail")))
			.list();
	}
}
