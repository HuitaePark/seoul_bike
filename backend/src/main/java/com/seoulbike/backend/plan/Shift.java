package com.seoulbike.backend.plan;

import java.time.Instant;
import java.time.LocalDate;
import java.time.LocalTime;
import java.time.format.DateTimeParseException;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

import org.springframework.http.HttpStatus;

import com.seoulbike.backend.common.ApiException;
import com.seoulbike.backend.common.SeoulTime;

/**
 * 근무조 ID `{regionId}:{yyyy-MM-dd}:{AM|PM|EVE}`와 그 시간 (합의 3: AM 07–10, PM 13–16, EVE 19–21시, 서울 시각).
 * 트럭은 근무조가 끝날 때까지 차고지로 돌아와야 한다 (asyncapi SolveTruck.availableUntil).
 */
record Shift(String id, String regionId, Instant start, Instant end) {

	private static final Pattern ID = Pattern.compile("([NS][0-9]{2}):([0-9]{4}-[0-9]{2}-[0-9]{2}):(AM|PM|EVE)");

	private enum Slot {
		AM(7, 10), PM(13, 16), EVE(19, 21);

		final int startHour;
		final int endHour;

		Slot(int startHour, int endHour) {
			this.startHour = startHour;
			this.endHour = endHour;
		}
	}

	/** 형식이 틀리면 400 INVALID_REQUEST. 구역이 있는지는 부르는 쪽이 본다. */
	static Shift parse(String id) {
		Matcher m = ID.matcher(id);
		if (!m.matches()) {
			throw invalid(id);
		}
		LocalDate day;
		try {
			day = LocalDate.parse(m.group(2));
		}
		catch (DateTimeParseException e) {
			throw invalid(id);
		}
		Slot slot = Slot.valueOf(m.group(3));
		return new Shift(id, m.group(1), at(day, slot.startHour), at(day, slot.endHour));
	}

	private static Instant at(LocalDate day, int hour) {
		return day.atTime(LocalTime.of(hour, 0)).atZone(SeoulTime.ZONE).toInstant();
	}

	private static ApiException invalid(String id) {
		return new ApiException(HttpStatus.BAD_REQUEST, "INVALID_REQUEST", "shiftId '" + id
				+ "'의 형식이 틀렸다. {구역 ID}:{yyyy-MM-dd}:{AM|PM|EVE}로 쓴다 (예 S05:2026-10-21:AM).");
	}
}
