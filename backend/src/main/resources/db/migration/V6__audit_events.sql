-- 감사 기록 (FR-18 "승인 없이 바뀐 것이 없음을 감사 로그로 증명", FR-08 질의 근거). 계약: internal-api GET /internal/shifts/{shiftId}/audit
-- append-only: 트리거가 수정·삭제를 막는다. 사람 이름은 남기지 않고 역할로 가린 표시만 남긴다 (actor_id는 내부 추적용, 밖으로 안 나감).
create table audit_events (
    audit_id      bigint generated always as identity primary key,
    shift_id      varchar(100) not null,
    occurred_at   timestamptz  not null default now(),
    type          varchar(32)  not null check (type in ('APPROVAL_APPROVED', 'APPROVAL_REJECTED', 'APPROVAL_REOPENED',
                                                        'FIELD_REPORT', 'CONSTRAINT_ADDED', 'PLAN_ACTIVATED',
                                                        'ROUTE_CHANGED')),
    actor_id      varchar(64),
    actor_role    varchar(16)  not null,
    actor_display varchar(64)  not null,
    target_id     varchar(100) not null,
    task_id       uuid,
    detail        varchar(500) not null
);

create index audit_events_shift_idx on audit_events (shift_id, occurred_at);

create function audit_events_append_only() returns trigger language plpgsql as $$
begin
    raise exception 'audit_events는 추가만 할 수 있다 (FR-18)';
end
$$;

create trigger audit_events_no_change
    before update or delete on audit_events
    for each row execute function audit_events_append_only();
