-- 경로 변경 승인 카드가 어느 후보 경로에 대한 것인지 기록한다.
-- 큰 변경(MAJOR)은 같은 후보에 관리자 → 기사 카드가 차례로 쌓이고, 적용 전에 둘 다 승인됐는지 본다.
-- 계약: docs/specs/README.md "propose_route_change", internal-api /internal/routes/{truckId}/apply
alter table approvals add column candidate_id varchar(64);
alter table approvals add column grade varchar(8) check (grade in ('MINOR', 'MAJOR'));

create index approvals_candidate_idx on approvals (task_id, candidate_id);
