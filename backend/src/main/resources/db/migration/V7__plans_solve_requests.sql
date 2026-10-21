-- 경로 계산 요청과 계획. 계약: asyncapi SolveRequest·SolveResult, internal-api PlanCandidate, openapi Plan
-- request_id는 {shiftId}:{planVersion}:{seq}. optimizer는 같은 id에 다른 내용이 오면 멈추므로 seq를 여기 남겨 다시 쓰지 않는다.
create table solve_requests (
    request_id      varchar(150) primary key,
    shift_id        varchar(100) not null,
    plan_version    integer      not null,
    seq             integer      not null,
    kind            varchar(8)   not null check (kind in ('PLAN', 'REPLAN')),
    task_id         uuid         references ai_tasks (task_id),
    request         jsonb        not null,
    status          varchar(8)   not null default 'PENDING' check (status in ('PENDING', 'OK', 'TIMEOUT', 'FAILED')),
    result          jsonb,
    result_event_id uuid         unique,
    requested_at    timestamptz  not null default now(),
    completed_at    timestamptz,
    unique (shift_id, plan_version, seq)
);

-- 계획. 후보(PROPOSED)는 결과 하나에 하나 생긴다. version은 근무조마다 1부터 올라간다.
create table plans (
    plan_id      varchar(64)  primary key,
    shift_id     varchar(100) not null,
    version      integer      not null,
    status       varchar(10)  not null check (status in ('DRAFT', 'PROPOSED', 'ACTIVE', 'REJECTED', 'CLOSED')),
    provisional  boolean      not null default false,
    routes       jsonb        not null,
    unserved     jsonb        not null,
    explanation  text,
    request_id   varchar(150) references solve_requests (request_id),
    created_at   timestamptz  not null default now(),
    activated_at timestamptz,
    activated_by varchar(64),
    unique (shift_id, version)
);

-- 근무조마다 활성 계획은 하나다.
create unique index plans_one_active_idx on plans (shift_id) where status = 'ACTIVE';
