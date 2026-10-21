package com.seoulbike.backend.common;

import java.time.Instant;
import java.time.OffsetDateTime;
import java.time.ZoneId;
import java.time.format.DateTimeFormatter;
import java.time.format.DateTimeParseException;

/**
 * backend가 내보내는 시각(API·Kafka·SSE)은 모두 서울 시각 +09:00으로 쓴다 (팀 합의 5). 받을 때는 어느 오프셋이든 받는다.
 * 같은 순간이라 저장(timestamptz)과 비교에는 영향이 없다. 화면·로그에서 사람이 바로 읽게 하려는 것이다.
 */
public final class SeoulTime {

	public static final ZoneId ZONE = ZoneId.of("Asia/Seoul");

	private SeoulTime() {
	}

	/** 예: 2026-10-21T07:00:00+09:00. 초 아래가 있으면 붙는다 (RFC 3339). */
	public static String format(Instant instant) {
		return DateTimeFormatter.ISO_OFFSET_DATE_TIME.format(instant.atZone(ZONE));
	}

	public static String now() {
		return format(Instant.now());
	}

	/**
	 * 다른 서비스가 만든 시각 문자열(예 agent의 occurredAt, optimizer의 eta)을 +09:00으로 다시 쓴다.
	 * 시각이 아니면 그대로 둔다. 형식 검증은 그 값을 쓰는 쪽이 한다.
	 */
	public static String normalize(String value) {
		try {
			return format(OffsetDateTime.parse(value).toInstant());
		}
		catch (DateTimeParseException e) {
			return value;
		}
	}

}
