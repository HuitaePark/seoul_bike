-- 대여소 조회·경로 계산이 대여소마다 "실시간 수집보다 늦은 가장 최근 현장 보고"를 찾는다 (StationRepository).
-- 기존 인덱스는 근무조 기준(shift_id, reported_at)이라 대여소로 찾을 때 쓰이지 않는다.
create index field_reports_station_idx on field_reports (station_id, reported_at desc);
