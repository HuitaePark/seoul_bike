<!--
Author: Jung Minkyu
Date: 20OCT2026
Purpose: Entry point of the OR-Tools guide; maps common points of confusion to the document that resolves them.
-->

# OR-Tools 재배치 파이프라인 이해 가이드

> 이 폴더는 "코드는 돌아가는데 **왜 그렇게 돌아가는지**, **OR-Tools가 어디서 뭘 하는지**, **발표에서 뭐라고 말해야 하는지** 모르겠다"는 상황을 해결하려고 만들었다.
> 그림은 GitHub에서 바로 렌더링되는 Mermaid로 그렸다. VS Code에서는 Markdown Preview Mermaid Support 확장이 필요할 수 있다.

## 먼저, 전체를 한 장으로

```mermaid
flowchart LR
    A["① 지금 상태<br/>대여소별 자전거 수"] --> B["② AI 예측<br/>다음 구간(N시간) 대여·반납"]
    B --> C["③ 불균형 계산<br/>어디서 몇 대를 빼고/채울까"]
    C --> D["④ OR-Tools<br/>트럭이 어떤 순서로 돌까"]
    D --> E["⑤ 작업 지시서<br/>트럭별 경로와 수량"]

    style D fill:#fde68a,stroke:#b45309,stroke-width:2px
```

한 문장으로: **AI가 "무슨 일이 생길지" 말하고, OR-Tools가 "그래서 어떻게 움직일지" 정한다.** 노란 상자(④)가 OR-Tools가 하는 일의 전부다.

## 헷갈리는 질문 → 읽을 문서

| 이런 게 헷갈린다면 | 읽을 곳 |
|---|---|
| 전체 구조가 어떻게 생겼고 파일이 각각 뭘 하는지 | [01-architecture.md](01-architecture.md) |
| 데이터가 단계마다 어떻게 변하는지 (실제 숫자로 보고 싶다) | [02-pipeline-walkthrough.md](02-pipeline-walkthrough.md) |
| OR-Tools가 코드 어디에서 어떻게 쓰이는지, 솔버 안에서 뭐가 일어나는지 | [03-ortools-explained.md](03-ortools-explained.md) |
| mock이 뭘 보여준 건지, 발표에서 어떻게 말할지, 예상 질문 | [04-explain-and-faq.md](04-explain-and-faq.md) |

## 추천 읽는 순서

1. **01 → 02**: 구조를 보고, 같은 구조 위에 실제 숫자가 흐르는 걸 따라간다. 여기까지 읽으면 "무슨 프로그램인지"는 잡힌다.
2. **03**: OR-Tools 부분만 확대해서 본다. 어렵게 느껴졌던 지점이다.
3. **04**: 이해한 걸 말로 바꾸는 연습.

## 먼저 알아두면 편한 용어 5개

| 용어 | 쉬운 뜻 |
|---|---|
| 재배치(rebalancing) | 자전거가 넘치는 대여소에서 모자란 대여소로 트럭으로 옮기는 일 |
| 예측 구간 (N시간) | 예측값이 몇 시간 동안의 대여·반납 수인지. 코드가 정하지 않고 예측기의 시간 단위가 정한다. 문서 예시는 N=1시간으로 가정 |
| 불균형(imbalance) | 앞으로 N시간 동안 "비거나 가득 찰" 대여소가 얼마나 옮겨야 하는지 (+수거 / -보충) |
| VRP | 여러 차량이 여러 지점을 도는 최적 경로 문제 (택배 배차와 같은 종류) |
| 제약(constraint) | 반드시 지켜야 하는 규칙. 예: 트럭에는 최대 20대 |
| 비용(cost) | 줄이고 싶은 값. 여기서는 이동거리 |

## 실행해 보기

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m rebalancing --hour 8 --trucks 3
```

이 폴더의 모든 숫자는 위 명령(`seed=7`, `hour=8`)을 실제로 실행해서 얻은 값이다.
