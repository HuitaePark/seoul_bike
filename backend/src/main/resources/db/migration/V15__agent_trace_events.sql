-- 에이전트 추론 기록 (관리자 로그). 계약: asyncapi ai.traces, openapi /api/admin/agent-traces (v0.16).
-- 작업 진행 이벤트(ai_task_events)와 따로 둔다: 작업 SSE·기사 화면으로 가지 않고 작업 상태·승인을 바꾸지 않는다.
-- 최선 노력 기록이라 작업 표에 외래 키를 걸지 않는다. kind·source도 제한하지 않는다 (agent가 종류를 늘려도 저장된다).
-- 보관 기간(app.agent-trace.retention, 기본 7일)이 지나면 AgentTraceStore가 지운다. 감사 기록(audit_events)이 아니다.
create table agent_trace_events (
    id          bigint generated always as identity primary key,
    event_id    uuid         not null unique,
    task_id     uuid         not null,
    shift_id    varchar(100) not null,
    trace_id    varchar(64),
    run_id      varchar(64),
    source      varchar(16)  not null,
    kind        varchar(24)  not null,
    turn        integer,
    title       varchar(300) not null,
    detail      jsonb        not null default '{}'::jsonb,
    occurred_at timestamptz  not null,
    received_at timestamptz  not null default now()
);

create index agent_trace_events_task_idx on agent_trace_events (task_id, id);
create index agent_trace_events_shift_idx on agent_trace_events (shift_id, id);
create index agent_trace_events_received_idx on agent_trace_events (received_at);
