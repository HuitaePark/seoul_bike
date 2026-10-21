# Author: Shin Junyeop
# Date: 20OCT2026
# Purpose: 따릉이 실시간 대여소 재고(bikeList)를 5분마다 스냅샷으로 쌓는다. 역산 재고 검증용.

"""따릉이 실시간 대여소 재고 수집 (M0, 6 데이터·시뮬레이터).

왜: 대여이력으로 복원한 재고(배치 직전 2대, 회수 직전 31대)를 실제 재고와 맞춰 보려면 재고 기록이 필요하다.
    공개 데이터에는 과거 재고가 없어서 지금부터 쌓는다. M4에 백엔드 수집 스케줄러로 넘긴다.
입력: 저장소 루트 .env의 SEOUL_API_KEY
출력: data/realtime/bikelist/<날짜>/<시분>.csv.gz  (스냅샷 1회 = 파일 1개, 대여소 1곳 = 1행, 시각은 KST)
실행: /usr/bin/python3 analysis/collect/bikelist.py   (표준 라이브러리만 쓴다. cron 등록 줄은 docs/roles/06-data-sim.md)
주의: bikeList의 list_total_count는 전체 건수가 아니라 그 페이지의 행 수다. 1,000행 미만 페이지를 마지막으로 본다.
"""
import csv
import datetime as dt
import fcntl
import gzip
import json
import os
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data/realtime/bikelist"
URL = "http://openapi.seoul.go.kr:8088/{key}/json/{service}/{a}/{b}/"
PAGE = 1000
MIN_ROWS = 2000  # 2026-10-20에 2,716곳. 이보다 훨씬 적으면 응답이 잘린 것으로 보고 저장하지 않는다
KST = dt.timezone(dt.timedelta(hours=9))
FIELDS = ["ts", "stationId", "stationName", "rackTotCnt", "parkingBikeTotCnt", "shared", "stationLatitude", "stationLongitude"]


def api_key():
    # 서버 compose 서비스(collector)는 환경 변수로 받는다. 노트북 cron은 그대로 저장소 루트 .env를 읽는다.
    if key := os.environ.get("SEOUL_API_KEY", "").strip():
        return key
    env = ROOT / ".env"
    for line in env.read_text().splitlines() if env.exists() else []:
        k, sep, v = line.partition("=")
        if sep and k.strip() == "SEOUL_API_KEY":
            return v.strip().strip("\"'")
    raise SystemExit("SEOUL_API_KEY가 환경 변수에도 .env에도 없다")


def get_page(key, a, b, service="bikeList", params=()):
    """서울 열린데이터광장 API의 a~b번째 행을 받는다. params는 주소 뒤에 붙는 값(예: 날짜, 시).
    네트워크 오류는 3번까지 다시 시도한다. 반환: 행 목록(데이터 없음이면 빈 목록)"""
    for attempt in range(3):
        try:
            with urllib.request.urlopen(URL.format(key=key, service=service, a=a, b=b) + "/".join(params), timeout=30) as r:
                js = json.load(r)
            break
        except Exception as e:  # 연결 오류, 키 오류 등으로 온 XML(JSON 해석 실패)
            err = str(e).replace(key, "***")  # 오류 메시지로 키가 로그에 남지 않게
            time.sleep(5 * (attempt + 1))
    else:
        raise RuntimeError(f"{service} {a}-{b}: {err}")
    if js.get("CODE") == "INFO-200":  # 마지막 페이지 다음 페이지: 데이터 없음
        return []
    # 응답 키가 서비스 이름과 다를 수 있어(bikeList → rentBikeStatus, tbCycleStationInfo → stationInfo) RESULT가 든 dict를 찾는다
    body = next((v for v in js.values() if isinstance(v, dict) and "RESULT" in v), {})
    if body.get("RESULT", {}).get("CODE") != "INFO-000":
        raise RuntimeError(f"{service} {a}-{b}: {js.get('RESULT') or body.get('RESULT') or js}")
    return body["row"]


def fetch_all(get):
    """get(a, b)로 페이지를 넘기며 모두 받는다. 1,000행 미만 페이지가 오면 끝."""
    rows, a = [], 1
    while True:
        page = get(a, a + PAGE - 1)
        rows += page
        if len(page) < PAGE:
            return rows
        a += PAGE


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    with open(OUT / ".lock", "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("이전 실행이 아직 돌고 있어 건너뜀")
            return
        now = dt.datetime.now(KST).replace(second=0, microsecond=0)
        key = api_key()
        rows = fetch_all(lambda a, b: get_page(key, a, b))
        if len(rows) < MIN_ROWS:
            raise RuntimeError(f"{now:%Y-%m-%d %H:%M} 행이 {len(rows)}개뿐이라 저장하지 않음")
        path = OUT / f"{now:%Y-%m-%d}" / f"{now:%H%M}.csv.gz"
        path.parent.mkdir(exist_ok=True)
        tmp = path.with_suffix(".tmp")
        with gzip.open(tmp, "wt", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, FIELDS, extrasaction="ignore")
            w.writeheader()
            w.writerows({**r, "ts": f"{now:%Y-%m-%d %H:%M}"} for r in rows)
        tmp.replace(path)  # 쓰다가 죽어도 반쪽 파일이 남지 않게
        print(f"{now:%Y-%m-%d %H:%M} {len(rows)}곳 저장")


if __name__ == "__main__":
    main()
