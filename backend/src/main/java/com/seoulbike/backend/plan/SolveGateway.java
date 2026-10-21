package com.seoulbike.backend.plan;

import java.time.Duration;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.TimeoutException;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpStatus;
import org.springframework.kafka.KafkaException;
import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.stereotype.Component;
import org.springframework.transaction.support.TransactionSynchronizationManager;
import org.springframework.transaction.support.TransactionTemplate;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ObjectNode;

import com.seoulbike.backend.common.ApiException;
import com.seoulbike.backend.common.SeoulTime;

/**
 * solve.requests로 경로 계산을 맡기고 solve.results를 기다린다 (계약 asyncapi SolveRequest·SolveResult).
 * /internal/plans/solve가 이것을 부른다.
 *
 * <p>requestId는 {shiftId}:{planVersion}:{seq}. planVersion은 지금 활성 계획의 version(없으면 0)이고 seq는 그 안에서 1부터다.
 * 요청 행을 먼저 커밋하고 발행하므로 결과가 요청보다 먼저 저장될 일은 없다.
 *
 * <p>기다리는 요청은 이 인스턴스의 메모리에 있다. 결과는 보통 이 인스턴스의 소비자가 넘겨준다. backend가 둘 떠 있는 순차 교체 중
 * (infra/deploy.sh)에는 solve.results를 다른 인스턴스가 받아 요청 행에만 저장할 수 있어서, 기다리는 동안 0.5초마다 요청 행도 본다.
 * 시간을 넘기면 기다림을 그만두고 TIMED_OUT을 돌려준다. 그 뒤에 온 결과도 요청 행에는 저장되지만 기다리는 쪽이 없어 후보는 만들지 않는다.
 * 후보는 결과를 받은 이 호출이 만든다. 요청마다 기다리는 호출은 하나이고 결과를 한 번만 가져가므로, 같은 결과가 두 번 와도 후보는 하나다.
 */
@Component
public class SolveGateway {

	public enum Kind {
		PLAN, REPLAN
	}

	private static final long SEND_TIMEOUT_SECONDS = 5;
	/** 다른 인스턴스가 저장한 결과를 찾으러 요청 행을 보는 간격 */
	private static final long POLL_NANOS = TimeUnit.MILLISECONDS.toNanos(500);
	private static final Set<String> ENVELOPE = Set.of("eventId", "schemaVersion", "occurredAt", "shiftId", "traceId",
			"requestId", "kind");

	private final KafkaTemplate<String, String> kafka;
	private final JsonMapper json;
	private final TransactionTemplate tx;
	private final PlanRepository plans;
	private final SolveRequestRepository requests;
	private final PlanCandidates candidates;
	private final Duration timeout;
	private final Map<String, CompletableFuture<JsonNode>> pending = new ConcurrentHashMap<>();

	SolveGateway(KafkaTemplate<String, String> kafka, JsonMapper json, TransactionTemplate tx, PlanRepository plans,
			SolveRequestRepository requests, PlanCandidates candidates, @Value("${app.solve.timeout}") Duration timeout) {
		this.kafka = kafka;
		this.json = json;
		this.tx = tx;
		this.plans = plans;
		this.requests = requests;
		this.candidates = candidates;
		this.timeout = timeout;
	}

	/**
	 * 경로를 계산한다. 결과가 올 때까지(최대 app.solve.timeout) 이 스레드가 기다린다.
	 *
	 * @param inputs 계약 SolveRequest 중 봉투 밖의 필드 (depot, asOf, stations, trucks, blockedStationIds, params)
	 * @param taskId 요청한 작업. 없으면 null. 현장 보고 작업이면 변경 등급을 FIELD_REPORT 기준으로 매긴다
	 * @param replan REPLAN이면 입력을 만든 활성 계획 (후보는 그 계획에 새 경로를 합친 것이다). PLAN이면 null
	 * @throws ApiException 503 QUEUE_UNAVAILABLE: 브로커가 요청을 받았다고 확인하지 않았다.
	 *         409 PLAN_CHANGED: 입력을 만든 뒤 활성 계획이 바뀌었다 (요청을 남기지 않는다)
	 */
	SolveOutcome solve(String shiftId, Kind kind, ObjectNode inputs, UUID taskId, String traceId,
			PlanCandidates.Replan replan) {
		// 결과를 최대 60초 기다리므로 바깥 트랜잭션 안에서 부르면 안 된다. 요청 행이 커밋되기 전에 발행되고,
		// 기다리는 동안 근무조 잠금과 DB 연결을 붙잡는다.
		if (TransactionSynchronizationManager.isActualTransactionActive()) {
			throw new IllegalStateException("SolveGateway.solve는 트랜잭션 밖에서 부른다");
		}
		for (String field : ENVELOPE) {
			if (inputs.has(field)) {
				throw new IllegalArgumentException("inputs에 봉투 필드 " + field + "를 넣을 수 없다. 봉투는 여기서 만든다");
			}
		}
		ObjectNode request = tx.execute(s -> persist(shiftId, kind, inputs, taskId, traceId, replan));
		String requestId = request.path("requestId").asString();

		// 결과가 발행 확인보다 먼저 올 수 있어 보내기 전에 기다릴 자리를 만든다.
		CompletableFuture<JsonNode> result = new CompletableFuture<>();
		pending.put(requestId, result);
		try {
			kafka.send(SolveTopics.SOLVE_REQUESTS, shiftId, json.writeValueAsString(request))
				.get(SEND_TIMEOUT_SECONDS, TimeUnit.SECONDS);
		}
		catch (InterruptedException e) {
			Thread.currentThread().interrupt();
			throw unsent(requestId);
		}
		catch (ExecutionException | TimeoutException | KafkaException e) {
			// KafkaException: 브로커가 내려가 있으면 producer가 이미 실패한 결과를 바로 돌려주고 send가 그 자리에서 던진다.
			throw unsent(requestId);
		}

		JsonNode answer;
		try {
			answer = await(requestId, result);
		}
		catch (InterruptedException e) {
			pending.remove(requestId, result);
			Thread.currentThread().interrupt();
			throw new IllegalStateException("경로 계산을 기다리다 중단됐다 (서버 종료): " + requestId, e);
		}
		catch (ExecutionException e) {
			throw new IllegalStateException("경로 계산 결과를 받지 못했다: " + requestId, e.getCause());
		}
		if (answer == null) {
			return new SolveOutcome(SolveOutcome.Status.TIMED_OUT, requestId, null, null, false, null);
		}

		String status = answer.path("status").asString();
		if (!"OK".equals(status)) {
			return new SolveOutcome(SolveOutcome.Status.valueOf(status), requestId, null, answer.path("code").asString(null),
					answer.path("retryable").asBoolean(false), answer.path("error").asString(null));
		}
		return new SolveOutcome(SolveOutcome.Status.OK, requestId,
				candidates.create(shiftId, requestId, taskId, answer, inputs.path("trucks"), replan), null, false, null);
	}

	/** 결과 저장이 커밋된 뒤 SolveResultConsumer가 부른다. 기다리는 요청이 없으면(시간 초과) 저장만 하고 끝난다. */
	void complete(SolveResultStore.Completed completed) {
		CompletableFuture<JsonNode> waiting = pending.remove(completed.requestId());
		if (waiting != null) {
			waiting.complete(completed.result());
		}
	}

	/**
	 * 결과를 기다린다. 이 인스턴스의 소비자가 넘겨주거나(complete), 다른 인스턴스가 요청 행에 저장한 것을 찾는다.
	 * 시간을 넘기면 null.
	 */
	private JsonNode await(String requestId, CompletableFuture<JsonNode> result)
			throws InterruptedException, ExecutionException {
		long deadline = System.nanoTime() + timeout.toNanos();
		while (true) {
			long left = deadline - System.nanoTime();
			try {
				return result.get(Math.max(1, Math.min(left, POLL_NANOS)), TimeUnit.NANOSECONDS);
			}
			catch (TimeoutException e) {
				var stored = requests.result(requestId);
				if (stored.isPresent()) {
					pending.remove(requestId, result);
					return json.readTree(stored.get());
				}
				if (System.nanoTime() - deadline >= 0) {
					// 결과 처리가 바로 앞에서 자리를 가져갔으면 곧 완료된다. 그 결과를 쓴다.
					return pending.remove(requestId, result) ? null : result.join();
				}
			}
		}
	}

	/** 근무조 잠금 안에서 seq를 매기고 요청 행을 PENDING으로 남긴다. 보낼 메시지를 그대로 저장한다. */
	private ObjectNode persist(String shiftId, Kind kind, ObjectNode inputs, UUID taskId, String traceId,
			PlanCandidates.Replan replan) {
		plans.lockShift(shiftId);
		int planVersion = plans.active(shiftId).map(PlanRepository.Active::version).orElse(0);
		// 트럭 위치·적재와 남은 정류장을 읽은 계획이 그사이 바뀌었으면 그 입력으로 계산한 후보는 새 계획을 덮어쓴다.
		if (replan != null && replan.version() != planVersion) {
			throw ApiException.conflict("PLAN_CHANGED", "근무조 " + shiftId + "의 활성 계획이 v" + replan.version() + "에서 v"
					+ planVersion + "로 바뀌었다. request_route_plan을 다시 부른다.");
		}
		int seq = requests.nextSeq(shiftId, planVersion);
		String requestId = shiftId + ":" + planVersion + ":" + seq;

		ObjectNode request = json.createObjectNode();
		request.put("eventId", UUID.randomUUID().toString());
		request.put("schemaVersion", 1);
		request.put("occurredAt", SeoulTime.now());
		request.put("shiftId", shiftId);
		if (traceId != null) {
			request.put("traceId", traceId);
		}
		request.put("requestId", requestId);
		request.put("kind", kind.name());
		request.setAll(inputs.deepCopy());
		seoulTimes(request);
		requests.insert(requestId, shiftId, planVersion, seq, kind, taskId, json.writeValueAsString(request));
		return request;
	}

	/** agent가 넘긴 시각도 같은 순간의 +09:00으로 보낸다 (팀 합의 5). optimizer는 어느 오프셋이든 받는다. */
	private static void seoulTimes(ObjectNode request) {
		if (request.path("asOf").isString()) {
			request.put("asOf", SeoulTime.normalize(request.path("asOf").asString()));
		}
		for (JsonNode truck : request.path("trucks")) {
			if (truck.path("availableUntil").isString()) {
				((ObjectNode) truck).put("availableUntil", SeoulTime.normalize(truck.path("availableUntil").asString()));
			}
		}
	}

	/**
	 * 브로커가 받았다고 확인하지 않았다. 실제로는 전달됐을 수 있어 요청 행은 지우지 않고 FAILED로 둔다
	 * (seq를 다시 쓰지 않는다). 그래도 결과가 오면 행에 저장된다.
	 */
	private ApiException unsent(String requestId) {
		pending.remove(requestId);
		requests.markUnsent(requestId);
		return new ApiException(HttpStatus.SERVICE_UNAVAILABLE, "QUEUE_UNAVAILABLE", "경로 계산 큐에 넣지 못했다. 잠시 뒤 다시 시도한다");
	}
}
