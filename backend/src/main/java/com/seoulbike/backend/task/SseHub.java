package com.seoulbike.backend.task;

import java.io.IOException;
import java.time.Duration;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.Set;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;
import java.util.function.IntFunction;

import org.springframework.context.SmartLifecycle;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;

/**
 * 작업별 SSE 구독자. 새 구독자는 먼저 등록하고 Last-Event-ID 이후 이벤트를 DB에서 다시 받는다.
 * 다시 보내는 동안 들어온 새 이벤트는 모아 두었다가 다시 보내기가 끝난 뒤 seq 순서로 보낸다.
 * 그래서 재연결 중에 이벤트가 빠지거나 두 번 가지 않는다.
 *
 * <p>backend가 둘 떠 있을 수 있다 (무중단 배포의 순차 교체 중, infra/deploy.sh). 그때는 ai.events를 두 인스턴스가 나눠 받아서
 * 이 인스턴스의 구독자가 볼 이벤트를 다른 인스턴스가 저장할 수 있다. 이벤트는 모두 DB에 seq 순서로 있으므로 DB로 메운다:
 * <ul>
 * <li>1초마다 구독 중인 작업의 새 이벤트를 DB에서 읽어 보낸다 ({@link #catchUp()}).
 * <li>보내려는 이벤트의 seq가 건너뛰면 그 사이를 DB에서 먼저 읽어 보낸다. 다른 인스턴스가 저장한 이벤트를 건너뛰지 않는다.
 * </ul>
 * 종료할 때는 웹 서버의 그레이스풀 종료보다 먼저 스트림을 닫는다. 화면은 Last-Event-ID로 다른 인스턴스에 다시 붙는다
 * (열린 SSE가 종료를 30초 붙잡지 않는다).
 */
@Component
class SseHub implements SmartLifecycle {

	private static final long STREAM_TIMEOUT_MS = Duration.ofMinutes(30).toMillis();

	private final ConcurrentHashMap<UUID, Set<Subscriber>> byTask = new ConcurrentHashMap<>();
	private final TaskEventStore store;
	private volatile boolean running;

	SseHub(TaskEventStore store) {
		this.store = store;
	}

	Subscriber subscribe(UUID taskId, int lastSeq) {
		Subscriber sub = new Subscriber(taskId, new SseEmitter(STREAM_TIMEOUT_MS), lastSeq, seq -> store.after(taskId, seq));
		// 종료 중에 들어온 구독: 쌓인 이벤트만 보내고 닫아 화면이 다른 인스턴스에 다시 붙게 한다 (종료를 붙잡지 않게)
		sub.closeAfterReplay = !running;
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

	/** 다른 backend 인스턴스가 저장한 이벤트를 1초 안에 보낸다. 다시 보내기가 끝난 구독자만 본다. */
	@Scheduled(fixedDelay = 1_000)
	void catchUp() {
		byTask.forEach((taskId, subs) -> {
			int from = subs.stream().filter(Subscriber::live).mapToInt(Subscriber::lastSeq).min().orElse(-1);
			if (from < 0) {
				return;
			}
			List<TaskEventStore.StoredEvent> events = store.after(taskId, from);
			if (!events.isEmpty()) {
				subs.forEach(s -> events.forEach(s::send));
			}
		});
	}

	/** 15초마다 주석 줄을 보내 프록시(Funnel·nginx)가 유휴 연결을 끊지 않게 한다. */
	@Scheduled(fixedRate = 15_000)
	void heartbeat() {
		byTask.values().forEach(subs -> subs.forEach(Subscriber::ping));
	}

	@Override
	public void start() {
		running = true;
	}

	/**
	 * 종료할 때 열린 스트림을 모두 닫는다. 단계(DEFAULT_PHASE)가 웹 서버 그레이스풀 종료보다 높아 먼저 불린다.
	 * 닫은 뒤에도 종료 중에 들어온 구독은 받는다 (곧 닫히고 화면이 다시 붙는다).
	 */
	@Override
	public void stop() {
		running = false;
		byTask.values().forEach(subs -> subs.forEach(Subscriber::close));
	}

	@Override
	public boolean isRunning() {
		return running;
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
		/** seq 다음부터의 이벤트를 DB에서 순서대로 읽는다. */
		private final IntFunction<List<TaskEventStore.StoredEvent>> after;
		private int lastSeq;
		private boolean closed;
		private boolean closeAfterReplay;
		/** null이면 다시 보내기가 끝났다. 그 전에 온 새 이벤트는 여기에 모은다. */
		private List<TaskEventStore.StoredEvent> pendingLive = new ArrayList<>();

		private Subscriber(UUID taskId, SseEmitter emitter, int lastSeq,
				IntFunction<List<TaskEventStore.StoredEvent>> after) {
			this.taskId = taskId;
			this.emitter = emitter;
			this.lastSeq = lastSeq;
			this.after = after;
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
			if (taskFinished || closeAfterReplay) {
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

		synchronized int lastSeq() {
			return lastSeq;
		}

		/** 다시 보내기가 끝났고 열려 있다. */
		synchronized boolean live() {
			return pendingLive == null && !closed;
		}

		private void write(TaskEventStore.StoredEvent event) {
			if (closed || event.seq() <= lastSeq) {
				return;
			}
			if (event.seq() > lastSeq + 1) {
				// 사이 이벤트를 다른 인스턴스가 저장했다. 작업마다 잠금을 잡고 seq를 매기므로 이 이벤트가 있으면 앞의 것도 DB에 있다.
				for (TaskEventStore.StoredEvent missed : after.apply(lastSeq)) {
					if (missed.seq() >= event.seq()) {
						break;
					}
					writeOne(missed);
				}
			}
			writeOne(event);
		}

		private void writeOne(TaskEventStore.StoredEvent event) {
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
