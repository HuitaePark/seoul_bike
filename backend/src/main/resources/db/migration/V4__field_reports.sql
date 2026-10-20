-- 현장 보고 (FR-11·12). 계약: openapi POST /api/reports, internal-api FieldReport
-- 기사가 정류장에서 남긴다. issue가 있으면 재경로 작업(task_id)이 같이 생긴다.
create table field_reports (
    report_id    uuid         primary key,
    shift_id     varchar(100) not null,
    truck_id     varchar(64)  not null,
    station_id   varchar(64)  not null,
    actual_bikes integer      not null check (actual_bikes >= 0),
    loaded       integer      check (loaded >= 0),
    unloaded     integer      check (unloaded >= 0),
    issue        varchar(24)  check (issue in ('BLOCKED', 'CONSTRUCTION', 'INVENTORY_MISMATCH', 'OTHER')),
    memo         varchar(500),
    reported_by  varchar(64)  not null references users (id),
    task_id      uuid         references ai_tasks (task_id),
    reported_at  timestamptz  not null default now()
);

create index field_reports_shift_idx on field_reports (shift_id, reported_at);
