# 가져온 코드와 역할 5의 변경

| 산출물 | 원본 | 가져온 범위 |
| --- | --- | --- |
| 현재 MCP 소스·테스트·근거 문서 | main, 94d86d18021996b9dc5a50dc6893ae63a1e4ad3f | business-mcp/, rag-mcp/, docs/MCP.md, docs/evidence/ |
| 최초 MCP 참조 스냅샷 | 유은/mcp, 4dd5305c32a4ebf22423e06965ae6cac947af4a1 | 당시 business-mcp/, rag-mcp/, docs/MCP.md, docs/rag/ |
| S05 참조 데이터 | junyeop, ad17528dcc0bf6a946642d678c53ab992f3bd683 | ref_regions.csv 중 S05 1행, ref_stations.csv 중 S05 196행 |
| 공통 명세 기준 | main, 94d86d18021996b9dc5a50dc6893ae63a1e4ad3f | 공개 API 0.5, Kafka 0.4, 내부 API 0.5 |

2026-10-21 main 병합에서 겹친 MCP 소스·테스트·문서는 최신 main 내용으로 맞췄다.
기존 docs/rag/는 최초 참조 스냅샷으로 남지만 실행·검증에는 docs/evidence/를 사용한다.
역할 5는 MCP 업무 규칙을 수정하지 않았으며 공통 명세도 main 원본을 유지한다.

역할 5가 추가한 것은 독립 Dockerfile·Compose·고정 의존성·HTTP 통합 테스트·클라이언트 예시,
S05 참조 시드 추출·검증 모듈·테스트와 관련 CI·문서다. 다른 사람의 코드 구현을 역할 5의 신규 구현으로 세지 않는다.

데이터는 CSV에서 S05 행만 선택하고 줄바꿈을 LF로 기록했다.
[추출 기록](../optimizer/seeds/source.json)에 원본 파일·추출 파일의 SHA-256과 행 수를 보관한다.
데이터 브랜치의 모델·시뮬레이터·requirements·기존 or-tools 모델 변경은 가져오지 않았다.

S05의 depot_lat·depot_lon은 analysis/seed.py가 대여소 좌표 평균으로 만든 값이다.
실제 운영 차고지 관측값이 아니며, 실제 위치·트럭 가용 대수·재고·예측은 별도로 확인해야 한다.

원본: [MCP 커밋](https://github.com/HuitaePark/skala_seoul_bike/commit/4dd5305c32a4ebf22423e06965ae6cac947af4a1),
[데이터 커밋](https://github.com/HuitaePark/skala_seoul_bike/commit/ad17528dcc0bf6a946642d678c53ab992f3bd683).
