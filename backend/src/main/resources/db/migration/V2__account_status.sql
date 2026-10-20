-- 회원가입(승인 대기)과 관리자 계정 관리. 계약: docs/specs/openapi.yaml (accounts)
alter table users add column status varchar(16) not null default 'ACTIVE'
    check (status in ('PENDING', 'ACTIVE', 'DISABLED'));

-- 가입 신청한 기사는 트럭을 아직 모른다. 트럭은 ACTIVE 기사에게만 반드시 있어야 한다.
alter table users drop constraint users_check;
alter table users add constraint users_active_driver_has_truck
    check (role <> 'DRIVER' or status <> 'ACTIVE' or truck_id is not null);

create index users_status_idx on users (status, created_at desc);
