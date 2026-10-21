-- 시드 기사 계정(driver1·driver2)을 시연 구역 N02의 트럭(TR-03·TR-04)으로 옮긴다. TR-01·TR-02는 N01 트럭이라
-- N02 계획에 경로가 없어 기사 화면이 비었다. 새 DB에서는 이 마이그레이션 뒤에 SeedUsers가 처음부터 TR-03·TR-04로 만든다.
-- 관리자가 이미 다른 트럭으로 바꾼 계정은 그대로 둔다. 바꾼 계정은 token_version을 올려 다시 로그인시킨다 (V12와 같은 이유).
update users set truck_id = 'TR-03', token_version = token_version + 1 where id = 'u-driver-1' and truck_id = 'TR-01';
update users set truck_id = 'TR-04', token_version = token_version + 1 where id = 'u-driver-2' and truck_id = 'TR-02';
