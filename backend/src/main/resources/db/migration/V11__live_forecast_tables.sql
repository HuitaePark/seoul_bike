-- 실시간 재고와 수요 예측 표. 6번(데이터)이 쓰고 backend는 읽기만 한다 (합의 12·13, 2026-10-21 결정 6).
-- 표는 4번이 Flyway로 만든다. 6번 수집기·예측기는 표를 만들거나 바꾸지 않는다.
-- 대여소 ID는 실시간 API ID(`ST-숫자`)다. 대여소번호(102 등)와 번호 체계가 달라 문자열로 바꾸지 않는다.
-- ref.stations(V10)로 FK를 걸지 않는다: 수집기가 주간 시드 갱신보다 먼저 새 대여소를 볼 수 있고,
-- 그 행을 버리면 재고가 빈다. 대여소 존재 확인은 읽는 쪽에서 한다.
create schema if not exists live;
create schema if not exists forecast;

-- 6번 수집기가 따릉이 실시간 API 결과를 5분마다 그대로 덧붙인다(대여소당 한 행). 고치거나 지우지 않는다.
-- backend는 대여소마다 가장 최근 행을 읽는다. PK 순서(station_id, observed_at)가 그 조회의 인덱스다.
-- 하루 약 78만 행(2,716곳 × 288회)씩 늘어난다. 보관 기간은 아직 정하지 않았다.
create table live.station_status (
    observed_at timestamptz not null,
    station_id  text        not null check (station_id ~ '^ST-[0-9]+$'),
    bikes       int         not null check (bikes >= 0),
    docks       int         not null check (docks >= 0),
    primary key (station_id, observed_at)
);

-- 6번 예측기가 근무조 시작 30분 전에 대여소마다 1~5시간 예측을 넣는다(합의 13·29). 같은 근무조는 늘 같은 값을
-- 돌려줘 재현된다(NFR-04). 계약: internal-api `Forecast` (/internal/forecasts, /internal/stations/risk).
-- shift_id는 `{regionId}:{yyyy-MM-dd}:{AM|PM|EVE}`. start는 예측 구간 시작 시각.
-- rentals·returns는 구간 합계, rentals_h·returns_h는 시간별 값(합의 15)이고 있으면 길이가 horizon_h다.
-- numeric은 'NaN'·'Infinity'를 받고 둘 다 >= 0이 참이다. NaN은 Infinity보다 크게 정렬되므로 < 'Infinity' 하나로
-- 둘을 같이 막는다. 배열은 NULL 원소가 있으면 0 <= ALL이 NULL(통과)이라
-- array_position으로 막는다. 시간별 합 = 합계는 실수 오차가 있어 강제하지 않는다.
create table forecast.station_forecasts (
    shift_id      text          not null,
    station_id    text          not null check (station_id ~ '^ST-[0-9]+$'),
    start         timestamptz   not null,
    horizon_h     int           not null check (horizon_h between 1 and 5),
    rentals       numeric       not null check (rentals >= 0 and rentals < 'Infinity'),
    returns       numeric       not null check (returns >= 0 and returns < 'Infinity'),
    rentals_h     numeric[]     check (rentals_h is null or (
        array_ndims(rentals_h) = 1 and array_lower(rentals_h, 1) = 1 and cardinality(rentals_h) = horizon_h
        and array_position(rentals_h, null) is null and 0 <= all (rentals_h) and 'Infinity' > all (rentals_h))),
    returns_h     numeric[]     check (returns_h is null or (
        array_ndims(returns_h) = 1 and array_lower(returns_h, 1) = 1 and cardinality(returns_h) = horizon_h
        and array_position(returns_h, null) is null and 0 <= all (returns_h) and 'Infinity' > all (returns_h))),
    model_version text          not null,
    created_at    timestamptz   not null default now(),
    primary key (shift_id, station_id, horizon_h)
);

-- /internal/forecasts는 대여소 ID·start·horizonH로 찾는다. PK는 shift_id가 앞이라 이 조회에 못 쓴다.
create index station_forecasts_station_start on forecast.station_forecasts (station_id, start);
