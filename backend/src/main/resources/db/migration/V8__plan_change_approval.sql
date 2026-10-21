-- 계획을 ACTIVE로 만든 승인. 계약: internal-api /internal/plans/{planId}/activate, /internal/routes/{truckId}/apply
-- 변경 API는 approvalId마다 한 번만 실행한다. agent가 같은 승인으로 다시 부르면 이 행을 찾아 처음 결과를 돌려준다.
-- unique라 같은 승인이 두 계획을 활성화할 수 없다 (동시에 불러도).
alter table plans add column activated_by_approval uuid unique references approvals (approval_id);

-- 경로 적용으로 만든 버전이면 어느 후보의 어느 트럭을 옮겨 왔는지. 계획 활성화로 ACTIVE가 된 행은 비어 있다.
-- 재시도가 같은 적용인지(같은 트럭·후보) 가린다.
alter table plans add column applied_candidate_id varchar(64);
alter table plans add column applied_truck_id varchar(64);
