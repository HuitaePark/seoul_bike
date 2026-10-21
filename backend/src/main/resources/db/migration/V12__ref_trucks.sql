-- 트럭 목록 (합의 30, 2026-10-21 결정). 트럭 ID는 서울 전체에서 이어 매기는 TR-NN(TR-01~TR-35)이다.
-- 행은 RefDataLoader가 ref_regions.csv의 trucks_min으로 채운다. Flyway는 로더보다 먼저 돌아 새 DB에는 구역 행이 없어서다.
-- 처음 채울 때 파일 행 순서(N01..N08, S01..S09)대로 번호를 매긴다. 주간 갱신으로 trucks_min이 바뀌면 번호를 다시 매기지 않는다:
-- 모자라면 그 구역의 비활성 트럭을 먼저 살리고, 그래도 모자라면 비어 있는 다음 번호로 새로 만든다.
-- 남으면 그 구역에서 번호가 큰 트럭부터 active=false로 둔다. 지난 계획·승인 기록이 그 ID를 가리키므로 지우지 않는다.
create table ref.trucks (
    truck_id    text primary key check (truck_id ~ '^TR-[0-9]{2,}$'),
    region_id   text not null references ref.regions (region_id),
    capacity    integer not null default 15 check (capacity > 0), -- 합의 19
    active      boolean not null default true,
    updated_at  timestamptz not null default now()
);

create index trucks_region_idx on ref.trucks (region_id);

-- 기존 기사 계정의 트럭 값 truck-N을 TR-0N(두 자리 이상)으로 바꾼다. users.truck_id에는 FK를 걸지 않는다:
-- 아래 규칙에 안 맞는 값이 남을 수 있고, 트럭 행은 이 마이그레이션 뒤에 로더가 채운다. 존재 확인은 승인·발급 때 한다.
-- 바꾼 계정은 token_version을 올린다. 예전 truckId가 든 토큰으로 남의 경로를 보지 못하게 다시 로그인시킨다.
-- truck-N 꼴이 아닌 값은 그대로 둔다. 시작할 때 RefDataLoader가 목록에 없는 트럭을 가진 기사 계정을 경고로 남기고, 관리자가 고친다.
-- 지난 승인·작업 기록의 truck-N은 기록이라 바꾸지 않는다.
update users
set truck_id = 'TR-' || lpad(n, greatest(2, length(n)), '0'),
    token_version = token_version + 1
from (select id as uid, (substring(truck_id from '^truck-([0-9]+)$'))::bigint::text as n
      from users
      where truck_id ~ '^truck-[0-9]+$') m
where users.id = m.uid;
