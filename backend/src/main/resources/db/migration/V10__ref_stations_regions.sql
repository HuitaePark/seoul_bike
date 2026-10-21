-- 기준 정보: 구역 17개와 대여소. 원본은 analysis/out/ref_regions.csv · ref_stations.csv (6번이 매주 갱신)이고
-- backend가 시작할 때 RefDataLoader가 이 표로 올린다. 화면·API·Kafka가 쓰는 대여소·구역 ID의 기준이다.
CREATE SCHEMA IF NOT EXISTS ref;

-- 구역 ID: 강북 N01~N08, 강남 S01~S09.
CREATE TABLE ref.regions (
    region_id   text PRIMARY KEY CHECK (region_id ~ '^[NS][0-9]{2}$'),
    name        text NOT NULL,
    side        text NOT NULL CHECK (side IN ('NORTH', 'SOUTH')),
    stations    integer NOT NULL,
    depot_lat   double precision NOT NULL,
    depot_lon   double precision NOT NULL,
    trucks_min  integer NOT NULL,
    updated_at  timestamptz NOT NULL DEFAULT now()
);

-- station_id는 실시간 API ID(ST-4 같은 꼴)다. 모든 API·Kafka·화면이 이 값을 쓴다 (팀 결정 2026-10-21).
-- 대여소번호(102 같은 꼴)는 번호 체계가 달라 따로 station_no에 둔다. 예: 번호 102 = 망원역 1번출구 앞 = ST-4,
-- ST-102는 번호 505(자양사거리)다. 둘 중 하나에서 다른 하나를 문자열로 만들지 않는다.
-- CSV 열 대응: api_station_id → station_id, station_id → station_no. 나머지는 이름 그대로.
-- 새 파일에 빠진 대여소는 지우지 않고 active=false로 둔다. 지난 계획이 그 대여소를 가리킨다.
-- station_no는 활성 대여소끼리만 겹치지 않는다. 빠진 대여소(비활성)의 번호를 다음 주 새 ST- ID가 다시 써도 올라가야 한다.
-- 검사는 커밋 때 한다. 두 대여소의 번호가 맞바뀌거나, 비활성으로 돌리기 전에 새 대여소가 같은 번호로 들어와도 한 트랜잭션 안에서 올릴 수 있다.
CREATE TABLE ref.stations (
    station_id   text PRIMARY KEY CHECK (station_id ~ '^ST-[0-9]+$'),
    station_no   text NOT NULL,
    name         text NOT NULL,
    region_id    text NOT NULL REFERENCES ref.regions (region_id),
    district     text NOT NULL,
    address      text NOT NULL,
    lat          double precision NOT NULL,
    lon          double precision NOT NULL,
    docks        integer NOT NULL,
    elevation_m  numeric NOT NULL,
    role         text NOT NULL CHECK (role IN ('FIXED_PICKUP', 'FIXED_DROPOFF', 'VARIABLE')),
    active       boolean NOT NULL DEFAULT true,
    updated_at   timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT stations_active_station_no_excl EXCLUDE USING btree (station_no WITH =) WHERE (active) DEFERRABLE INITIALLY DEFERRED
);

CREATE INDEX stations_region_idx ON ref.stations (region_id);
-- 번호로 찾을 때 (비활성 포함). 위 제약의 인덱스는 활성 행만 담는다.
CREATE INDEX stations_station_no_idx ON ref.stations (station_no);

-- 마지막으로 올린 파일 내용의 SHA-256. 같은 파일로 다시 시작하면 적재를 건너뛴다.
CREATE TABLE ref.source_files (
    file_name  text PRIMARY KEY,
    sha256     text NOT NULL,
    loaded_at  timestamptz NOT NULL DEFAULT now()
);
