package com.seoulbike.backend.task;

import java.io.IOException;
import java.time.Duration;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.Set;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;

import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;

/**
 * 작업별 SSE 구독자. 새 구독자는 먼저 등록하고 Last-Event-ID 이후 이벤트를 DB에서 다시 받는다.
 * 다시 보내는 동안 들어온 새 이벤트는 모아 두었다가 다시 보내기가 끝난 뒤 seq 순서로 보낸다.
 * 그래서 재연결 중에 이벤트가 빠지거나 두 번 가지 않는다.
 *
 * <p>주의: backend 인스턴스가 하나라는 전제다. 여러 개를 띄우면 각 인스턴스가 ai.events를 모두 받아야 한다.
 */
@Component
class SseHub {

	private static final long STREAM_TIMEOUT_MS = Duration.ofMinutes(30).toMillis();

	private final ConcurrentHashMap<UUID, Set<Subscriber>> byTask = new ConcurrentHashMap<>();

	Subscriber subscribe(UUID taskId, int lastSeq) {
		Subscriber sub = new Subscriber(taskId, new SseEmitter(STREAM_TIMEOUT_MS), lastSeq);
		byTask.computeIfAbsent(taskId, k -> ConcurrentHashMap.newKeySet()).add(sub);
		Runnable remove = () -> unsubscribe(sub);
		sub.emitter.onCompletion(remove);
		sub.emitter.onTimeout(remove);
		sub.emitter.onError(e -> remove.run());
		return sub;
	}

	void publish(TaskEventStore.StoredEvent event) {
		Set<Subscriber> subs = byTask.get(event.taskId());
		if (subs != null) {
			subs.forEach(s -> s.send(event));
		}
	}

	/** 15초마다 주석 줄을 보내 프록시(Funnel·nginx)가 유휴 연결을 끊지 않게 한다. */
	@Scheduled(fixedRate = 15_000)
	void heartbeat() {
		byTask.values().forEach(subs -> subs.forEach(Subscriber::ping));
	}

	private void unsubscribe(Subscriber sub) {
		byTask.computeIfPresent(sub.taskId, (k, subs) -> {
			subs.remove(sub);
			return subs.isEmpty() ? null : subs;
		});
	}

	static final class Subscriber {

		private final UUID taskId;
		private final SseEmitter emitter;
		private int lastSeq;
		private boolean closed;
		/** null이면 다시 보내기가 끝났다. 그 전에 온 새 이벤트는 여기에 모은다. */
		private List<TaskEventStore.StoredEvent> pendingLive = new ArrayList<>();

		private Subscriber(UUID taskId, SseEmitter emitter, int lastSeq) {
			this.taskId = taskId;
			this.emitter = emitter;
			this.lastSeq = lastSeq;
		}

		SseEmitter emitter() {
			return emitter;
		}

		/** DB에 쌓인 이벤트를 보내고, 그동안 모인 새 이벤트를 이어서 보낸다. 한 번만 부른다. */
		synchronized void replay(List<TaskEventStore.StoredEvent> history, boolean taskFinished) {
			history.forEach(this::write);
			List<TaskEventStore.StoredEvent> live = pendingLive;
			pendingLive = null;
			live.stream().sorted(Comparator.comparingInt(TaskEventStore.StoredEvent::seq)).forEach(this::write);
			if (taskFinished) {
				close();
			}
		}

		synchronized void send(TaskEventStore.StoredEvent event) {
			if (pendingLive != null) {
				pendingLive.add(event);
				return;
			}
			write(event);
		}

		private void write(TaskEventStore.StoredEvent event) {
			if (closed || event.seq() <= lastSeq) {
				return;
			}
			try {
				emitter.send(SseEmitter.event().id(String.valueOf(event.seq())).name(event.type()).data(event.data()));
				lastSeq = event.seq();
				if (event.terminal()) {
					close();
				}
			}
			catch (IOException | IllegalStateException e) {
				closed = true;
			}
		}

		synchronized void ping() {
			if (closed) {
				return;
			}
			try {
				emitter.send(SseEmitter.event().comment("ping"));
			}
			catch (IOException | IllegalStateException e) {
				closed = true;
			}
		}

		private void close() {
			if (!closed) {
				closed = true;
				emitter.complete();
			}
		}
	}
}
