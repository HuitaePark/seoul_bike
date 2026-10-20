package com.seoulbike.backend.task;

import java.net.URI;
import java.util.UUID;

import jakarta.validation.Valid;
import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Size;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;

import com.seoulbike.backend.auth.CurrentUser;

/**
 * 작업 API 5개 (과제 레퍼런스 표 5-8, 계약 docs/specs/openapi.yaml).
 * {@code @Validated}를 붙이지 않는다. 붙이면 Spring 7 내장 파라미터 검증이 꺼져 범위 오류가 500이 된다.
 */
@RestController
class TaskController {

	record CreateTaskRequest(@NotNull TaskType type, @NotBlank @Size(max = 100) String shiftId,
			@Size(max = 2000) String text) {
	}

	private final TaskService service;
	private final TaskEventStore events;
	private final SseHub hub;

	TaskController(TaskService service, TaskEventStore events, SseHub hub) {
		this.service = service;
		this.events = events;
		this.hub = hub;
	}

	@PostMapping("/api/ai/tasks")
	ResponseEntity<TaskService.TaskAccepted> create(@AuthenticationPrincipal Jwt jwt,
			@Valid @RequestBody CreateTaskRequest req) {
		var accepted = service.create(CurrentUser.from(jwt), new TaskService.CreateTask(req.type(), req.shiftId(), req.text()));
		return ResponseEntity.accepted().location(URI.create("/api/ai/tasks/" + accepted.taskId())).body(accepted);
	}

	@GetMapping("/api/ai/tasks")
	TaskRepository.Page list(@AuthenticationPrincipal Jwt jwt, @RequestParam(defaultValue = "false") boolean mine,
			@RequestParam(required = false) TaskStatus status, @RequestParam(required = false) String shiftId,
			@RequestParam(defaultValue = "0") @Min(0) int page,
			@RequestParam(defaultValue = "20") @Min(1) @Max(100) int size) {
		return service.list(CurrentUser.from(jwt), mine, status, shiftId, page, size);
	}

	@GetMapping("/api/ai/tasks/{taskId}")
	Task get(@AuthenticationPrincipal Jwt jwt, @PathVariable UUID taskId) {
		return service.get(CurrentUser.from(jwt), taskId);
	}

	/**
	 * 구독을 먼저 등록하고 DB에 쌓인 이벤트를 다시 보낸다. 그동안 들어온 새 이벤트는 SseHub가 모아 뒤에 보낸다.
	 * 이미 끝난 작업이면 남은 이벤트를 보내고 닫는다.
	 */
	@GetMapping(path = "/api/ai/tasks/{taskId}/stream", produces = MediaType.TEXT_EVENT_STREAM_VALUE)
	SseEmitter stream(@AuthenticationPrincipal Jwt jwt, @PathVariable UUID taskId,
			@RequestHeader(value = "Last-Event-ID", required = false) String lastEventId) {
		Task task = service.get(CurrentUser.from(jwt), taskId);
		int after = parseSeq(lastEventId);
		SseHub.Subscriber sub = hub.subscribe(taskId, after);
		sub.replay(events.after(taskId, after), task.status().terminal());
		return sub.emitter();
	}

	@PostMapping("/api/ai/tasks/{taskId}/approval")
	ResponseEntity<Void> decide(@AuthenticationPrincipal Jwt jwt, @PathVariable UUID taskId,
			@Valid @RequestBody ApprovalDecision decision) {
		service.decide(CurrentUser.from(jwt), taskId, decision);
		return ResponseEntity.accepted().build();
	}

	private static int parseSeq(String lastEventId) {
		if (lastEventId == null || lastEventId.isBlank()) {
			return 0;
		}
		try {
			return Integer.parseInt(lastEventId.trim());
		}
		catch (NumberFormatException e) {
			return 0;
		}
	}
}
