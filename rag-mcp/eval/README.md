# 근거 문서 검색 점검

| 항목 | 내용 |
| --- | --- |
| 날짜 | 2026-10-20 |
| 문서 | `docs/evidence` 4개, 조각 42개 (`ops-rebalancing-standard` v2 기준) |
| 실행 | `python eval/retrieval_eval.py` (메모리 BM25), `VECTOR_DSN=... python eval/retrieval_eval.py` (pgvector 색인도) |
| 환경 | Apple M 계열 CPU, pgvector/pgvector:pg16 컨테이너, fastembed 0.9 (onnxruntime 1.31) |

질문마다 "답이 되는 근거가 있는 문서와 절"을 정답으로 두고, 상위 1개·3개 안에 정답이 있는지 센다.
질문은 일부러 문서와 다른 표현으로 썼다 (예 "트럭 한 대에 자전거를 최대 몇 대 실을 수 있어?" ↔ 문서 "트럭 용량 15대").

## 1. 임베딩 모델 고르기

`intfloat/multilingual-e5-large`를 기본으로 정했다. 같은 10문항(점검 질문)으로, 문서 표현을 고치기 전에 비교했다.

| 모델 (fastembed, ONNX) | 차원 | 벡터만 1위 | 벡터만 3위 안 | 혼합 1위 | 혼합 3위 안 | 메모리 | 질문 1개 임베딩 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| paraphrase-multilingual-MiniLM-L12-v2 | 384 | 2/10 | 6/10 | 6/10 | 10/10 | 약 0.8GB | 약 3ms |
| paraphrase-multilingual-mpnet-base-v2 | 768 | 5/10 | 8/10 | 7/10 | 9/10 | 약 1.9GB | 약 7ms |
| **multilingual-e5-large** | 1024 | **7/10** | **9/10** | **7/10** | **10/10** | 약 1.6GB | 약 36ms |

- 같은 조건의 키워드(BM25)만: 1위 7/10, 3위 안 10/10.
- 혼합 방식도 비교했다 (e5-large 기준). 0.7 x 코사인 + 0.3 x 키워드(현재), 둘 다 정규화한 0.5 + 0.5, RRF(k=60) 모두 1위 7/10이었고 3위 안은 10/10, 10/10, 10/10. 가장 단순한 현재 방식을 남겼다.
- 메모리는 프로세스 최대 상주 메모리. compose로 띄운 rag-mcp 컨테이너는 약 2.0GB였다.

## 2. 문서 표현 고치기

1위를 놓친 문항 중 두 개는 문서에 팀 용어가 없어서였다. PRD 표현을 문서에 더했다 (`ops-rebalancing-standard` v1 -> v2).

| 질문 | 원인 | 고침 (근거) |
| --- | --- | --- |
| 관리자 승인이 필요한 큰 변경은 어떤 경우야? | 문서에는 `major`만 있고 "큰 변경"이 없음 | 등급 표에 "작은 변경·큰 변경" (PRD FR-07) |
| 기사가 운전하는 동안 변경안 알림을 띄워도 돼? | 문서는 "운행 중"만 씀 | "운전 중 확인을 강요하지 않는다" (PRD FR-14 제목) |

## 3. 결과

| 질문 묶음 | 키워드 (메모리 BM25) | 혼합 (pgvector, e5-large) |
| --- | --- | --- |
| 점검 질문 10개 (문서 고칠 때 씀) | 1위 9/10, 3위 안 10/10 | 1위 9/10, 3위 안 10/10 |
| **확인용 질문 8개 (조정에 안 씀, 한 번만 실행)** | **1위 5/8, 3위 안 7/8** | **1위 5/8, 3위 안 7/8** |

해석
- 점검 질문은 문서를 고칠 때 썼으므로 점수가 높게 나온다. 일반 성능은 확인용 질문 쪽을 본다.
- 지금처럼 문서가 4개, 조각이 42개인 작은 묶음에서는 혼합 검색이 키워드 검색보다 낫다고 할 근거가 없다. 둘이 같은 점수다.
- 그래도 pgvector·임베딩 색인을 기본으로 둔 이유: ROLES의 RAG 요구(Embedding·Indexing), 문서가 늘고 질문 표현이 다양해질 때 낱말이 겹치지 않는 근거를 찾는 능력(벡터만으로 1위 7/10). 키워드 점수를 섞어 대여소 ID·숫자 질문을 보완한다.
- 확인용 질문에서 놓친 것: "현장 재고가 앱이랑 다르면 어떤 제약으로 바꿔?"(정답 `INVENTORY_OVERRIDE`가 있는 제약 조건 절)를 두 방식 모두 못 찾았다. 다음 개선 후보다.

## 4. 다시 돌리는 방법

```bash
docker run -d --name rag-pg -e POSTGRES_PASSWORD=test -e POSTGRES_DB=vec -p 55432:5432 pgvector/pgvector:pg16
```

```bash
cd rag-mcp && pip install -e ".[test]"
```

```bash
VECTOR_DSN=postgresql://postgres:test@127.0.0.1:55432/vec rag-indexer
```

```bash
VECTOR_DSN=postgresql://postgres:test@127.0.0.1:55432/vec python eval/retrieval_eval.py
```
