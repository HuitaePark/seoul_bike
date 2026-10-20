-- 승인된 제약 (internal-api POST /internal/constraints, FR-09). 관리자가 승인한 ADD_CONSTRAINT 카드 하나에 제약 하나.
-- agent가 같은 호출을 다시 보내도(재시도) 처음 만든 제약을 돌려주도록 approval_id를 유일하게 둔다.
create table constraints (
    constraint_id uuid         primary key,
    approval_id   uuid         not null unique references approvals (approval_id),
    task_id       uuid         not null references ai_tasks (task_id),
    shift_id      varchar(100) not null,
    type          varchar(24)  not null check (type in ('TARGET_ADJUST', 'STATION_SUSPENDED', 'STATION_BLOCKED',
                                                        'INVENTORY_OVERRIDE', 'PRIORITY_BOOST')),
    station_ids   jsonb        not null,
    value         integer,
    valid_from    timestamptz  not null,
    valid_to      timestamptz  not null,
    reason        varchar(500) not null,
    created_at    timestamptz  not null default now(),
    check (valid_to > valid_from)
);

create index constraints_shift_idx on constraints (shift_id, valid_from);
