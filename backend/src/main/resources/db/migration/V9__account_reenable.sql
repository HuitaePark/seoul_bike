-- 비활성 계정 다시 열기 (POST /api/admin/users/{userId}/enable, 이슈 #53).
-- approved_at: 승인(또는 관리자 발급)된 시각. 가입 거절도 DISABLED로 남으므로, 이 값이 없으면 승인된 적 없는 신청이라 다시 열지 않는다.
--              다시 열면 신청 때 고른 역할로 승인 단계 없이 ACTIVE가 되기 때문이다.
-- token_version: 비활성화할 때 올린다. 토큰의 tv 클레임과 다르면 401이라, 다시 열어도 비활성화 전에 받은 토큰은 쓰지 못한다.
alter table users add column approved_at timestamptz;
alter table users add column token_version integer not null default 0;

-- 승인 시각을 따로 남기지 않았으므로 지금 ACTIVE인 계정은 만든 시각으로 채운다.
update users set approved_at = created_at where status = 'ACTIVE';
-- 이미 DISABLED인 계정은 승인 뒤 비활성화인지 가입 거절인지 알 수 없다. 트럭이 있는 기사만 승인을 거친 것이 확실하다
-- (트럭은 승인 때 정한다). 나머지는 다시 열 수 없고, 다시 가입하거나 새로 발급한다.
update users set approved_at = created_at where status = 'DISABLED' and role = 'DRIVER' and truck_id is not null;
-- 지금 비활성인 계정의 토큰(tv 없음 = 0)은 다시 열어도 쓰지 못하게 한다.
update users set token_version = 1 where status = 'DISABLED';
