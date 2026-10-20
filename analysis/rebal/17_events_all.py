"""재배치 역산 17단계: 대형 행사를 유형별로 묶어 대여·반납·재고·트럭 대응을 비교 (팀 프로젝트 탐색용 임시 파일)

무엇을: 행사장 반경 700m 대여소에서, 행사일과 같은 요일 앞뒤 4주(행사일 제외) 중앙값을 비교한다.
        도착 = 행사 전 반납(시작 4시간 전 ~ 시작), 퇴장 = 끝난 뒤 2시간 대여, 재고 = 끝나는 시각의 복원 재고 합,
        빈 대여소 = 퇴장 2시간 동안 재고 0이 된 대여소 수, 미리 채움 = 당일 0시 ~ 끝 시각 배치, 사후 회수 = 끝 ~ 다음 날 12시 회수.
행사 날짜·시각: 웹 확인. 시작 시각 미확인은 평일 19시·토 18시·일 17시로 가정, 끝 = 공연 시작 + 3시간, 경기 + 2시간.
대여소 폐쇄형(불꽃축제·탄핵 선고)은 이용 감소만 따로 본다.
입력: 대여이력 2024·2025(읽기만), out/gaps_<연도>, visits_<연도>, bike_pick/drop(_2024) / 출력: out/events_all.csv, 화면
"""
from pathlib import Path

import duckdb
import pandas as pd

HERE = Path(__file__).parent
O = HERE / "out"
R = "/Users/junyeop/dareungi"
V = {"상암": (37.5683, 126.8972), "KSPO": (37.5203, 127.1268), "고척": (37.4982, 126.8671),
     "여의도": (37.5284, 126.9326), "광화문": (37.5720, 126.9769)}


def conc(name, venue, dates, kind, start=None, dur=3):
    out = []
    for d in dates:
        dow = pd.Timestamp(d).dayofweek
        s = start if start is not None else (17 if dow == 6 else 18 if dow == 5 else 19)
        out.append((kind, name, venue, d, s - 4, s, s + dur, s + dur + 2))
    return out


E = []  # (유형, 이름, 행사장, 날짜, 도착 시작시, 도착 끝시, 퇴장 시작시, 퇴장 끝시)
E += conc("한국-쿠웨이트", "상암", ["2025-06-10"], "A 스타디움 경기", 20, 2)
E += conc("FC서울-바르셀로나(6.2만)", "상암", ["2025-07-31"], "A 스타디움 경기", 20, 2)
E += conc("토트넘-뉴캐슬(6.5만)", "상암", ["2025-08-03"], "A 스타디움 경기", 20, 2)
E += conc("아이콘매치(6만)", "상암", ["2025-09-13", "2025-09-14"], "A 스타디움 경기", 19, 2)
E += conc("한국-파라과이(2.2만)", "상암", ["2025-10-14"], "A 스타디움 경기", 20, 2)
E += conc("아이유 상암", "상암", ["2024-09-21", "2024-09-22"], "B 스타디움 콘서트", 19)
E += conc("임영웅 상암", "상암", ["2024-05-25", "2024-05-26"], "B 스타디움 콘서트", 18)
E += conc("아이유 KSPO", "KSPO", ["2024-03-02", "2024-03-03", "2024-03-09", "2024-03-10"], "C 돔·아레나 콘서트")
E += conc("임영웅 KSPO", "KSPO", ["2025-11-21", "2025-11-22", "2025-11-23", "2025-11-28", "2025-11-29", "2025-11-30"], "C 돔·아레나 콘서트")
E += conc("SMTOWN 고척", "고척", ["2025-01-11", "2025-01-12"], "C 돔·아레나 콘서트", 17, 4)
E += conc("이세계 페스티벌 고척", "고척", ["2025-05-15", "2025-05-16", "2025-05-17"], "C 돔·아레나 콘서트")
E += conc("PLAVE 고척", "고척", ["2025-11-21", "2025-11-22"], "C 돔·아레나 콘서트")
E += conc("지드래곤 고척", "고척", ["2025-12-12", "2025-12-13", "2025-12-14"], "C 돔·아레나 콘서트")
E += [("D 한 방향 대규모 이동", "자전거대행진(도착)", "상암", "2025-05-11", 8, 12, 12, 14),
      ("E 집결 출발", "JTBC 마라톤(출발)", "상암", "2025-11-02", 5, 8, 8, 10)]
CLOSE = [("불꽃축제(여의도)", "여의도", "2025-09-27"), ("탄핵 선고(광화문)", "광화문", "2025-04-04")]
ev_dates = {(e[2], e[3]) for e in E} | {(v, d) for _, v, d in CLOSE}

c = duckdb.connect()
c.execute("CREATE TABLE venue(v VARCHAR, lat DOUBLE, lon DOUBLE)")
c.executemany("INSERT INTO venue VALUES (?, ?, ?)", [(k, *x) for k, x in V.items()])
c.execute(f"""CREATE TABLE vs AS SELECT v, st_no FROM venue, read_csv('{R}/data/interim/station_master.csv') s WHERE s.st_no IS NOT NULL AND
 2 * 6371000 * asin(sqrt(pow(sin(radians(s.lat - venue.lat) / 2), 2) + cos(radians(venue.lat)) * cos(radians(s.lat)) * pow(sin(radians(s.lon - venue.lon) / 2), 2))) <= 700""")
P = f"read_parquet(['{R}/data/processed/rent/y=2024/*/*.parquet', '{R}/data/processed/rent/y=2025/*/*.parquet'])"
c.execute(f"""CREATE TABLE ev AS SELECT vs.v, e.ts, e.x FROM (SELECT rent_st st, rent_dt ts, -1 x FROM {P}
  UNION ALL SELECT rtn_st, rtn_dt, 1 FROM {P} WHERE rtn_st IS NOT NULL) e JOIN vs ON vs.st_no = e.st""")
c.execute(f"""CREATE TABLE vv AS SELECT vs.v, kind, n, lo + (hi - lo) / 2 m FROM read_parquet(['{O}/visits_2024.parquet', '{O}/visits_2025.parquet']) x
  JOIN vs ON vs.st_no = x.st""")
# 재고 복원(행사장 대여소만, 연도별 파일)
c.execute(f"""CREATE TABLE iv AS
WITH g AS (SELECT * FROM read_parquet(['{O}/gaps_2024.parquet', '{O}/gaps_2025.parquet'])
           WHERE from_st IN (SELECT st_no FROM vs) OR to_st IN (SELECT st_no FROM vs)),
p AS (SELECT bike_id, t_to, lo + (hi - lo) / 2 m FROM read_parquet(['{O}/bike_pick.parquet', '{O}/bike_pick_2024.parquet'])),
d AS (SELECT bike_id, t_to, lo + (hi - lo) / 2 m FROM read_parquet(['{O}/bike_drop.parquet', '{O}/bike_drop_2024.parquet']))
SELECT from_st st, t_from s, t_to e FROM g WHERE NOT moved AND from_st IN (SELECT st_no FROM vs)
UNION ALL SELECT g.from_st, g.t_from, p.m FROM g JOIN p USING (bike_id, t_to) WHERE g.from_st IN (SELECT st_no FROM vs)
UNION ALL SELECT g.to_st, d.m, g.t_to FROM g JOIN d USING (bike_id, t_to) WHERE g.to_st IN (SELECT st_no FROM vs)""")
c.execute("""CREATE TABLE ser AS SELECT st, ts, sum(dx) OVER (PARTITION BY st ORDER BY ts, dx ROWS UNBOUNDED PRECEDING) stock,
  lead(ts) OVER (PARTITION BY st ORDER BY ts, dx) ts_next FROM (SELECT st, s ts, 1 dx FROM iv UNION ALL SELECT st, e, -1 FROM iv)""")
c.execute("CREATE TABLE sv AS SELECT ser.*, vs.v FROM ser JOIN vs ON vs.st_no = ser.st")


def metric(v, d, a0, a1, d0, d1):
    T = f"TIMESTAMP '{d}'"
    return c.sql(f"""SELECT
      (SELECT count(*) FROM ev WHERE v = '{v}' AND x = 1 AND ts >= {T} + INTERVAL {a0} HOUR AND ts < {T} + INTERVAL {a1} HOUR),
      (SELECT count(*) FROM ev WHERE v = '{v}' AND x = -1 AND ts >= {T} + INTERVAL {d0} HOUR AND ts < {T} + INTERVAL {d1} HOUR),
      (SELECT coalesce(sum(stock), 0) FROM (SELECT arg_max(stock, ts) stock FROM sv WHERE v = '{v}' AND ts <= {T} + INTERVAL {d0} HOUR GROUP BY st)),
      (SELECT count(DISTINCT st) FROM sv WHERE v = '{v}' AND stock = 0 AND ts < {T} + INTERVAL {d1} HOUR AND ts_next > {T} + INTERVAL {d0} HOUR),
      (SELECT coalesce(sum(n), 0) FROM vv WHERE v = '{v}' AND kind = 'drop' AND m >= {T} AND m < {T} + INTERVAL {d0} HOUR),
      (SELECT coalesce(sum(n), 0) FROM vv WHERE v = '{v}' AND kind = 'pick' AND m >= {T} + INTERVAL {d0} HOUR AND m < {T} + INTERVAL 36 HOUR)
    """).fetchone()


cols = ["도착", "퇴장", "재고", "빈대여소", "미리채움", "사후회수"]
rows = []
for kind, name, v, d, a0, a1, d0, d1 in E:
    e = metric(v, d, a0, a1, d0, d1)
    base = [metric(v, str((pd.Timestamp(d) + pd.Timedelta(days=7 * k)).date()), a0, a1, d0, d1)
            for k in (-4, -3, -2, -1, 1, 2, 3, 4) if (v, str((pd.Timestamp(d) + pd.Timedelta(days=7 * k)).date())) not in ev_dates]
    b = pd.DataFrame(base, columns=cols).median()
    rows.append({"유형": kind, "행사": name, "날짜": d, **{k: e[i] for i, k in enumerate(cols)}, **{f"평소_{k}": b[k] for k in cols}})
df = pd.DataFrame(rows)
df.to_csv(O / "events_all.csv", index=False)
n_st = dict(c.sql("SELECT v, count(*) FROM vs GROUP BY 1").fetchall())
print("행사장 대여소 수:", n_st)
show = df.assign(**{f"{k}배": (df[k] / df[f"평소_{k}"].where(df[f"평소_{k}"] > 0)).round(1) for k in ["도착", "퇴장", "재고"]})
print(show[["유형", "행사", "날짜", "도착", "평소_도착", "퇴장", "평소_퇴장", "재고", "평소_재고", "빈대여소", "평소_빈대여소",
            "미리채움", "평소_미리채움", "사후회수", "평소_사후회수"]].to_string(index=False))
print("\n== 유형별 중앙값(행사일 / 평소)")
g = df.groupby("유형")
summ = pd.DataFrame({"행사일수": g.size()})
for k in cols:
    summ[k] = g[k].median().round(0).astype(int).astype(str) + " / " + g[f"평소_{k}"].median().round(0).astype(int).astype(str)
summ["퇴장배수"] = g.apply(lambda x: (x["퇴장"] / x["평소_퇴장"].clip(lower=1)).median()).round(1)
print(summ.to_string())

print("\n== 대여소 폐쇄형: 행사장 700m 하루 대여+반납(행사일 / 같은 요일 앞뒤 4주 중앙값)")
for name, v, d in CLOSE:
    use = lambda day: c.sql(f"SELECT count(*) FROM ev WHERE v = '{v}' AND ts::DATE = DATE '{day}'").fetchone()[0]
    base = sorted(use(str((pd.Timestamp(d) + pd.Timedelta(days=7 * k)).date())) for k in (-4, -3, -2, -1, 1, 2, 3, 4))
    print(f"{name} {d}: {use(d)} / {(base[3] + base[4]) / 2:.0f}")
