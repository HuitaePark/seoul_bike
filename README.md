# seoul_bike
스칼라 AI 캡스톤 리포지토리

공유자전거 재배치 에이전트: Agent가 재배치 계획을 트럭 경로까지 세우고, 기사의 현장 보고로 남은 경로를 다시 짜며, 최종 결정은 사람이 내리는 운영 도구.

## 아키텍처

![아키텍처 구성도](docs/images/architecture.png)

자체 서버 한 대에서 Docker Compose로 운영한다. 서비스끼리는 Redpanda(Kafka 호환)로 이벤트를 주고받고, 상태의 원본은 PostgreSQL이다.

| 계층 | 스택 |
| --- | --- |
| 에이전트 | LangGraph (근무조 그래프, 사람 승인 `interrupt`) |
| 최적화 | OR-Tools (트럭 경로·회수·배치 대수) |
| 백엔드 | Python 3.12, FastAPI |
| 메시징 | Redpanda |
| 저장 | PostgreSQL + PostGIS, Parquet + DuckDB |
| 이동 시간 | OSRM |
| 웹 | Next.js (관제 대시보드, 기사 PWA) |

## 문서

- [PRD](docs/PRD.md): 문제 정의, 요구사항, 에이전트 설계
- [아키텍처](docs/ARCHITECTURE.md): 스택, 모듈 구조, 메시징, 컨테이너 구성
- [OR-Tools 파이프라인 가이드](docs/or-tools/README.md)
