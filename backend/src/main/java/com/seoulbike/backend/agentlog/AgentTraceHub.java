package com.seoulbike.backend.agentlog;

import java.io.IOException;
import java.time.Duration;
import java.util.Set;
import java.util.concurrent.ConcurrentHashMap;

import org.springframework.context.SmartLifecycle;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;
import tools.jackson.databind.json.JsonMapper;

/**
 * 관리자 로그 실시간 스트림. 구독자마다 마지막으로 보낸 id를 들고 1초마다 그 뒤 기록을 DB에서 읽어 보낸다.
 *
 * <p>DB에서 읽기만 하므로 backend가 둘 떠 있어도(무중단 배포 중) 어느 인스턴스가 저장한 기록이든 같은 순서로 받는다
 * (AgentTraceStore가 id 순서대로 커밋한다). 작업 SSE(SseHub)와 구독자·스레드를 나누지 않고 따로 둔다: 관리자 로그가 많아도
 * 작업 화면의 스트림은 영향을 받지 않는다. 구독자가 없으면 DB를 읽지 않는다.
 */
@Component
class AgentTraceHub implements SmartLifecycle {

	private static final long STREAM_TIMEOUT_MS = Duration.ofMinutes(30).toMillis();
	private static final int BATCH = 200;

	private final Set<Subscriber> subscribers = ConcurrentHashMap.newKeySet();
	private final AgentTraceStore store;
	private final JsonMapper json;
	private volatile boolean running;

	AgentTraceHub(AgentTraceStore store, JsonMapper json) {
		this.store = store;
		this.json = json;
	}

	/** lastId 다음 기록부터 보낸다. null이면 지금 마지막 기록 다음부터 (지난 기록은 목록 API로 읽는다). */
	SseEmitter subscribe(AgentTraceStore.Filter filter, Long lastId) {
		Subscriber sub = new Subscriber(new SseEmitter(STREAM_TIMEOUT_MS), filter,
				lastId == null ? store.lastId() : lastId);
		if (!running) {
			// 종료 중에 들어온 구독은 바로 닫아 화면이 다른 인스턴스에 다시 붙게 한다
			sub.close();
			return sub.emitter;
		}
		subscribers.add(sub);
		Runnable remove = () -> subscribers.remove(sub);
		sub.emitter.onCompletion(remove);
		sub.emitter.onTimeout(remove);
		sub.emitter.onError(e -> remove.run());
		sub.ping(); // 새 기록이 없어도 응답을 바로 시작한다 (화면·CLI가 연결된 것을 안다)
		return sub.emitter;
	}

	@Scheduled(fixedDelay = 1_000)
	void poll() {
		subscribers.forEach(sub -> {
			AgentTraceStore.Page page = store.after(sub.filter, sub.lastId(), BATCH);
			page.items().forEach(trace -> sub.send(trace.id(), trace.kind(), json.writeValueAsString(trace)));
			if (sub.closed()) {
				subscribers.remove(sub);
			}
		});
	}

	/** 15초마다 주석 줄을 보내 프록시(Funnel·nginx)가 유휴 연결을 끊지 않게 한다. */
	@Scheduled(fixedRate = 15_000)
	void heartbeat() {
		subscribers.forEach(Subscriber::ping);
	}

	int subscriberCount() {
		return subscribers.size();
	}

	@Override
	public void start() {
		running = true;
	}

	@Override
	public void stop() {
		running = false;
		subscribers.forEach(Subscriber::close);
		subscribers.clear();
	}

	@Override
	public boolean isRunning() {
		return running;
	}

	private static final class Subscriber {

		private final SseEmitter emitter;
		private final AgentTraceStore.Filter filter;
		private long lastId;
		private boolean closed;

		private Subscriber(SseEmitter emitter, AgentTraceStore.Filter filter, long lastId) {
			this.emitter = emitter;
			this.filter = filter;
			this.lastId = lastId;
		}

		synchronized long lastId() {
			return lastId;
		}

		synchronized boolean closed() {
			return closed;
		}

		synchronized void send(long id, String kind, String data) {
			if (closed || id <= lastId) {
				return;
			}
			try {
				emitter.send(SseEmitter.event().id(String.valueOf(id)).name(kind).data(data));
				lastId = id;
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
