# Author: Shin Junyeop
# Date: 20OCT2026
# Purpose: 기상청 과거 단기예보(발표 시각별)를 표 하나로 정리한다. 예측기가 '예측 시점 이전에 나온 예보'를 피처로 쓰게 한다.

"""과거 단기예보 정리 (예측 개선, PRD 7.3).

예측기는 지금까지 그 시각에 실제로 온 비(관측)를 썼다. 운영할 때는 예보만 있으므로, 평가도 예보로 해야 정직하다.
원본: dareungi/data/raw/forecast_short/new/*.zip (기상자료개방포털, 종로구 교남동 격자 60·127, 2021-06-30 ~ 2026-10-06).
      zip마다 변수별 CSV. 'Start : YYYYMMDD' 줄 뒤에 'day,hour,forecast,value' 줄이 온다. day·hour는 발표 시각(UTC),
      forecast는 발표 뒤 몇 시간인지. -999는 결측. 읽는 규칙은 dareungi/py/forecast/kma_forecast.py와 같다.
출력: data/shared/weather_fcst.parquet (issue 발표 시각 KST, target 대상 시각 KST, temp ℃, pcp mm/h, pop %)
실행: .venv/bin/python analysis/weather_fcst.py
"""
import os
import re
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DAR = Path(os.environ.get("DAREUNGI", "/Users/junyeop/dareungi"))
VARS = {"1시간기온": "temp", "1시간강수량": "pcp", "강수확률": "pop"}


def zip_name(i: zipfile.ZipInfo) -> str:
    return i.filename if i.flag_bits & 0x800 else i.filename.encode("cp437").decode("cp949", "replace")


def parse(lines: list[str]) -> list[tuple[datetime, datetime, float]]:
    """(발표 시각, 대상 시각, 값). 시각은 KST. 깨진 줄은 건너뛴다(포털 내보내기의 2~4%)."""
    out, start = [], None
    for line in lines:
        m = re.search(r"Start\s*:\s*(\d{8})", line)
        if m:
            start = datetime.strptime(m.group(1), "%Y%m%d")
            continue
        p = [x.strip() for x in line.split(",")]
        if start is None or len(p) < 4 or not re.fullmatch(r"\d{1,2}", p[0]) or not re.fullmatch(r"\d{4}", p[1]):
            continue
        try:
            day, hour, lead, val = int(p[0]), int(p[1][:2]), int(p[2]), float(p[3])
        except ValueError:
            continue
        if val <= -999 or not 1 <= day <= 31:
            continue
        month = start.replace(day=1)
        if day < start.day:  # 월말에 시작한 블록이 다음 달로 넘어간 경우
            month = (month + timedelta(days=32)).replace(day=1)
        try:
            issue = month.replace(day=day) + timedelta(hours=hour + 9)  # UTC → KST
        except ValueError:
            continue
        out.append((issue, issue + timedelta(hours=lead), val))
    return out


def main() -> None:
    parts = {v: [] for v in VARS.values()}
    for z in sorted((DAR / "data/raw/forecast_short/new").glob("*.zip")):
        zf = zipfile.ZipFile(z)
        for i in zf.infolist():
            name = zip_name(i)
            var = VARS.get(name.split("_")[1])
            if var and "202103_202106" not in name:  # 2021-03~06 구형식은 시각이 이미 KST다. 2024~2025만 쓰므로 건너뛴다
                parts[var] += parse(zf.open(i).read().decode("utf-8-sig").splitlines())
    frames = [pd.DataFrame(rows, columns=["issue", "target", v]).drop_duplicates(["issue", "target"], keep="last").set_index(["issue", "target"])
              for v, rows in parts.items()]
    df = pd.concat(frames, axis=1).reset_index().sort_values(["issue", "target"])
    # zip 이름이 내려받은 시각(YYYYMMDDhhmmss)이다. 그보다 뒤에 발표된 예보는 월말 블록을 다음 달로 잘못 읽은 줄이라 버린다
    got = max(datetime.strptime(z.stem, "%Y%m%d%H%M%S") for z in (DAR / "data/raw/forecast_short/new").glob("*.zip"))
    df = df[df.issue <= got]
    out = ROOT / "data/shared/weather_fcst.parquet"
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out, index=False)
    print(f"{len(df):,}행, 발표 {df.issue.min()} ~ {df.issue.max()}, 발표 횟수 {df.issue.nunique():,}")
    print(df[["temp", "pcp", "pop"]].describe().round(2).to_string())
    print(f"저장: {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
