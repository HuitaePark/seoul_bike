# 근거 문서 (docs/evidence)

에이전트가 답변·계획 설명의 근거로 찾는 문서다. `rag-indexer`가 이 폴더의 `*.md`(이 README 제외)를 색인하고, `rag-mcp`가
`search_docs` 도구와 Resource `doc://{doc_id}`로 돌려준다. 위치와 머리말은 [합의 21번](../data/agreements.md)을 따른다.

## 문서 추가·수정

1. 파일 이름은 `doc_id`와 같게 한다 (`rebal-summary.md` → `doc_id: rebal-summary`).
2. 맨 위 머리말 (필수 6개, 이 순서)

   | 필드 | 뜻 |
   | --- | --- |
   | `title` | 문서 제목 |
   | `doc_id` | 파일 이름과 같은 ID |
   | `version` | 내용이 바뀌면 올린다. 색인에 함께 저장된다 |
   | `updated` | 원문과 대조했거나 고친 날 (`YYYY-MM-DD`) |
   | `source` | 사람이 읽는 출처 한 줄 |
   | `status` | 추정치·가상 값 같은 한계. 답변에도 함께 밝힌다 |

3. 선택 필드 (있으면 MCP가 더 정확히 쓴다)

   | 필드 | 뜻 |
   | --- | --- |
   | `doc_type` | 검색 거르기: `ops_standard`, `seoul_notice`, `reverse_analysis`, `handover`, `reference_paper`, `general` |
   | `sources[]` | 출처 여러 개를 기본(`role: primary`, 팀 저장소)과 보조(`role: supplementary`, 논문·보도자료)로 나눔 |
   | `data_period` | 수치의 기준 기간 (오래된 수치를 거르기 위해) |

4. 본문은 `##` 절 하나만 읽혀도 뜻이 통하게 쓴다. 절이 검색 단위다.
5. 저장소 밖 자료에서 온 문단은 앞에 `` `[보조]` ``를 붙이고, 절 끝에 `출처:` 줄을 둔다. 보조 문단의 출처는 그 문단에 이름(저자·기관)이나
   `doc_id`가 나오는 보조 출처만 붙는다.
6. 머지되면 서버 배포 때 `rag-indexer`가 다시 색인한다. 문서와 임베딩 모델이 그대로면 건너뛴다.

## 지금 있는 문서

| doc_id | 내용 | 작성 |
| --- | --- | --- |
| `ops-rebalancing-standard` | 재배치 운영 기준 (근무조, 목표 재고, 제약, 변경 등급, 현장 보고) | 3 MCP·RAG |
| `notice-seoul-fireworks-2025` | 서울시 공지: 2025 불꽃축제 공유자전거 대여·반납 중단 | 3 MCP·RAG |
| `handover-shift-summary-template` | 근무조 인수인계 요약 양식 | 3 MCP·RAG |
| `ref-forecast-kim-2023` | 수요예측 논문 요약 (보조 근거) | 3 MCP·RAG |
| `rebal-summary` | 따릉이 트럭 재배치 역산 요약 | 6 데이터 (PR #10) |
