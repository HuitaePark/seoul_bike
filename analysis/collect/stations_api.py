# Author: Shin Junyeop
# Date: 20OCT2026
# Purpose: 대여소 정보 API 두 개의 응답 원본을 날짜별로 저장한다. 대여소 마스터(analysis/stations.py)의 입력.

"""대여소 정보 원본 수집 (M1).

왜: 대여소는 새로 생기고 없어진다. 가공한 표만 남기면 나중에 가공 방식을 바꿀 때 그날의 원본을 다시 얻을 수 없어서,
    API 응답 행을 그대로 날짜별로 남긴다.
입력: .env의 SEOUL_API_KEY
출력: data/raw/stations/<날짜>/tbCycleStationInfo.json.gz  (대여소번호, ST-ID, 이름, 자치구, 주소, 좌표, 거치대 수)
      data/raw/stations/<날짜>/bikeStationMaster.json.gz   (ST-ID, 주소, 좌표. 앞 표에 없는 좌표를 보충)
실행: /usr/bin/python3 analysis/collect/stations_api.py   (표준 라이브러리만 쓴다. 주 1회면 충분)
"""
import datetime as dt
import gzip
import json

from bikelist import KST, ROOT, api_key, fetch_all, get_page

SERVICES = ("tbCycleStationInfo", "bikeStationMaster")
MIN_ROWS = 3000  # 2026-10-20에 각각 3,246행, 3,433행. 훨씬 적으면 응답이 잘린 것으로 보고 저장하지 않는다


def main():
    key = api_key()
    out = ROOT / "data/raw/stations" / f"{dt.datetime.now(KST):%Y-%m-%d}"
    out.mkdir(parents=True, exist_ok=True)
    for svc in SERVICES:
        rows = fetch_all(lambda a, b: get_page(key, a, b, svc))
        if len(rows) < MIN_ROWS:
            raise RuntimeError(f"{svc} 행이 {len(rows)}개뿐이라 저장하지 않음")
        tmp = out / f"{svc}.tmp"
        with gzip.open(tmp, "wt", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False)
        tmp.replace(out / f"{svc}.json.gz")
        print(f"{svc} {len(rows)}행 저장")


if __name__ == "__main__":
    main()
