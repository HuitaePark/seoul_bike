-- 작업 API(202 · SSE · 승인)와 인증에 필요한 최소 테이블. 계약: docs/specs/openapi.yaml

create table users (
    id            varchar(64)  primary key,
    login_id      varchar(64)  not null unique,
    password_hash varchar(100) not null,
    name          varchar(100) not null,
    role          varchar(16)  not null check (role in ('MANAGER', 'DRIVER', 'EXECUTIVE')),
    truck_id      varchar(64),
    created_at    timestamptz  not null default now(),
    check (role <> 'DRIVER' or truck_id is not null)
);

create table ai_tasks (
    task_id             uuid         primary key,
    type                varchar(16)  not null check (type in ('PLAN_DRAFT', 'FIELD_REPORT', 'REQUEST', 'QUESTION')),
    status              varchar(20)  not null check (status in ('QUEUED', 'RUNNING', 'WAITING_APPROVAL', 'DONE', 'FAILED')),
    shift_id            varchar(100) not null,
    input_text          text,
    created_by          varchar(64)  not null references users (id),
    created_at          timestamptz  not null default now(),
    updated_at          timestamptz  not null default now(),
    answer              text,
    sources             jsonb        not null default '[]',
    error               text,
    trace_id            varchar(64),
    pending_approval_id uuid
);
create index ai_tasks_created_by_idx on ai_tasks (created_by, created_at desc);
create index ai_tasks_shift_idx on ai_tasks (shift_id, created_at desc);

-- 진행 이벤트. seq는 backend가 저장할 때 매기고 SSE id로 쓴다. 같은 eventId는 한 번만 저장한다.
create table ai_task_events (
    task_id     uuid        not null references ai_tasks (task_id),
    seq         integer     not null,
    event_id    uuid        not null unique,
    type        varchar(20) not null,
    payload     jsonb       not null,
    occurred_at timestamptz not null,
    primary key (task_id, seq)
);

create table approvals (
    approval_id   uuid         primary key,
    task_id       uuid         not null references ai_tasks (task_id),
    kind          varchar(20)  not null check (kind in ('SUBMIT_PLAN', 'ADD_CONSTRAINT', 'ROUTE_CHANGE')),
    approver_role varchar(16)  not null check (approver_role in ('MANAGER', 'DRIVER')),
    truck_id      varchar(64),
    status        varchar(16)  not null check (status in ('PENDING', 'APPROVED', 'REJECTED', 'SUPERSEDED')),
    request       jsonb        not null,
    decision      jsonb,
    decided_by    varchar(64)  references users (id),
    decided_at    timestamptz,
    created_at    timestamptz  not null default now()
);
create index approvals_task_idx on approvals (task_id);
