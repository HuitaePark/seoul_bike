package com.seoulbike.backend.plan;

import tools.jackson.databind.node.ObjectNode;

/**
 * 경로 계산 한 번의 결과.
 *
 * @param candidate OK일 때만 있는 계약 PlanCandidate. 나머지는 null
 * @param code optimizer가 FAILED·TIMEOUT과 함께 보낸 실패 종류 (asyncapi SolveResult.code, 예 NO_SOLUTION). 나머지는 null
 * @param retryable 같은 입력으로 다시 계산할 만한가 (optimizer 판단). OK·TIMED_OUT은 false
 * @param error optimizer가 보낸 사람이 읽는 설명. 분기는 code로 한다
 */
public record SolveOutcome(Status status, String requestId, ObjectNode candidate, String code, boolean retryable,
		String error) {

	public enum Status {
		/** 후보를 만들었다. */
		OK,
		/** optimizer가 풀지 못했다. */
		FAILED,
		/** optimizer가 제한 시간 안에 풀지 못했다고 답했다. */
		TIMEOUT,
		/** optimizer가 app.solve.timeout 안에 답하지 않았다. 늦은 결과는 요청 행에만 저장되고 후보는 생기지 않는다. */
		TIMED_OUT
	}
}
