package com.seoulbike.backend.ref;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/**
 * 기준 정보 CSV(analysis/out/ref_*.csv)를 머리글 이름으로 읽는다. 대여소 이름에 쉼표가 들어가 따옴표로 감싼 칸이 있다
 * ("염창역 3, 4번 출구 사이"). 열 순서가 바뀌어도 이름으로 찾는다.
 */
final class RefCsv {

	private RefCsv() {
	}

	static List<Map<String, String>> parse(String text) {
		List<List<String>> records = records(text.startsWith("\uFEFF") ? text.substring(1) : text);
		if (records.isEmpty()) {
			throw new IllegalArgumentException("CSV에 머리글이 없다");
		}
		List<String> header = records.getFirst();
		List<Map<String, String>> rows = new ArrayList<>(records.size() - 1);
		for (int i = 1; i < records.size(); i++) {
			List<String> fields = records.get(i);
			if (fields.size() != header.size()) {
				throw new IllegalArgumentException("CSV %d번째 줄의 칸 수가 %d개다 (머리글 %d개)".formatted(i + 1, fields.size(), header.size()));
			}
			Map<String, String> row = HashMap.newHashMap(header.size());
			for (int c = 0; c < header.size(); c++) {
				row.put(header.get(c), fields.get(c));
			}
			rows.add(row);
		}
		return rows;
	}

	private static List<List<String>> records(String text) {
		List<List<String>> records = new ArrayList<>();
		List<String> fields = new ArrayList<>();
		StringBuilder field = new StringBuilder();
		boolean quoted = false;
		for (int i = 0; i < text.length(); i++) {
			char ch = text.charAt(i);
			if (quoted) {
				if (ch != '"') {
					field.append(ch);
				} else if (i + 1 < text.length() && text.charAt(i + 1) == '"') {
					field.append('"');
					i++;
				} else {
					quoted = false;
				}
			} else if (ch == '"') {
				quoted = true;
			} else if (ch == ',') {
				fields.add(field.toString());
				field.setLength(0);
			} else if (ch == '\n' || ch == '\r') {
				if (ch == '\r' && i + 1 < text.length() && text.charAt(i + 1) == '\n') {
					i++;
				}
				fields.add(field.toString());
				field.setLength(0);
				addUnlessBlank(records, fields);
				fields = new ArrayList<>();
			} else {
				field.append(ch);
			}
		}
		fields.add(field.toString());
		addUnlessBlank(records, fields);
		return records;
	}

	private static void addUnlessBlank(List<List<String>> records, List<String> fields) {
		if (!(fields.size() == 1 && fields.getFirst().isEmpty())) {
			records.add(fields);
		}
	}
}
