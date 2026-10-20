package com.seoulbike.backend.task;

import java.io.IOException;
import java.time.Duration;
import java.util.Set;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;

import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;

/**
 * 작업별 SSE 구독자. 재연결한 구독자는 Last-Event-ID 이후 이벤트를 DB에서 다시 받고,
 * 새 이벤트와 다시 보내기가 겹쳐도 seq로 걸러 한 번씩만 받는다.
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

		private Subscriber(UUID taskId, SseEmitter emitter, int lastSeq) {
			this.taskId = taskId;
			this.emitter = emitter;
			this.lastSeq = lastSeq;
		}

		SseEmitter emitter() {
			return emitter;
		}

		synchronized void send(TaskEventStore.StoredEvent event) {
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

		synchronized void close() {
			if (!closed) {
				closed = true;
				emitter.complete();
			}
		}
	}
}
