"""Reference seed validation protects identity mapping and avoids fabricated state."""
import csv
import hashlib
import json
from pathlib import Path

import pytest

from rebalancing.seeds import load_region_seed, load_region_seeds, main

ROOT = Path(__file__).resolve().parents[2]
SEEDS = ROOT / "infra/optimizer/seeds"
REGIONS = ROOT / "analysis/out/ref_regions.csv"
STATIONS = ROOT / "analysis/out/ref_stations.csv"


def load(regions=None, stations=None, region="S05"):
    return load_region_seed(regions or REGIONS, stations or STATIONS, region)


def rewrite(source, target, change):
    with source.open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        rows = list(reader)
        fields = list(reader.fieldnames)
    change(rows)
    with target.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    return target


def test_s05_reference_matches_source_and_preserves_both_ids():
    metadata = json.loads((SEEDS / "source.json").read_text())
    assert metadata["status"] == "reference-geography"
    for file in metadata["files"]:
        content = (ROOT / file["sourcePath"]).read_bytes()
        assert hashlib.sha256(content).hexdigest() == file["sourceSha256"]
    seed = load()
    assert len(seed.stations) == 196 and seed.trucks_min == 3
    assert seed.depot == (37.52006, 126.903308)
    assert seed.id_map(source="data")["200"] == "ST-45"
    assert seed.id_map(source="api")["ST-45"] == "200"
    summary = seed.summary()
    assert summary["referenceOnly"] is True and summary["referenceDepot"] == list(seed.depot)
    assert summary["regionId"] == "S05"
    assert 37 < summary["bounds"]["minLat"] <= summary["bounds"]["maxLat"] < 38
    assert summary["bounds"]["minLon"] <= seed.depot[1] <= summary["bounds"]["maxLon"]
    assert not {"bikes", "priority", "neededBikes", "asOf"} & summary["stations"][0].keys()
    assert not {"asOf", "availableUntil", "load"} & summary.keys()


def test_id_mapping_requires_an_explicit_direction():
    with pytest.raises(ValueError, match="ID source"):
        load().id_map(source="canonical")


@pytest.mark.parametrize("field,value", [
    ("lat", "nan"), ("lon", "181"), ("lat", "invalid"),
    ("docks", "0"), ("docks", "2.5"), ("role", "UNKNOWN"), ("name", " "),
    ("station_id", ""), ("api_station_id", ""), ("station_id", "ST-4"),
    ("api_station_id", "102"), ("api_station_id", "ST-４"), ("region_id", "N99"),
])
def test_bad_station_rows_are_rejected(tmp_path, field, value):
    path = rewrite(STATIONS, tmp_path / "stations.csv",
                   lambda rows: rows[0].__setitem__(field, value))
    with pytest.raises(ValueError):
        load(stations=path)


@pytest.mark.parametrize("field", ["station_id", "api_station_id"])
def test_duplicate_ids_never_collapse_to_one_station(tmp_path, field):
    path = rewrite(STATIONS, tmp_path / "stations.csv",
                   lambda rows: rows[1].__setitem__(field, rows[0][field]))
    with pytest.raises(ValueError, match="unique"):
        load(stations=path)


@pytest.mark.parametrize("field,value", [("stations", "195"), ("depot_lat", "91"), ("trucks_min", "0")])
def test_bad_region_metadata_is_rejected(tmp_path, field, value):
    path = rewrite(REGIONS, tmp_path / "region.csv",
                   lambda rows: rows[0].__setitem__(field, value))
    with pytest.raises(ValueError):
        load(regions=path)


def test_unknown_region_and_missing_columns_are_reported(tmp_path):
    with pytest.raises(ValueError, match="unknown region_id"):
        load(region="N99")
    path = tmp_path / "stations.csv"
    path.write_text("station_id,name\n200,test\n")
    with pytest.raises(ValueError, match="columns"):
        load(stations=path)


def test_duplicate_region_and_inconsistent_row_width_are_rejected(tmp_path):
    regions = rewrite(REGIONS, tmp_path / "regions.csv",
                      lambda rows: rows.append(dict(rows[0])))
    with pytest.raises(ValueError, match="duplicate region"):
        load(regions=regions)
    stations = tmp_path / "stations.csv"
    stations.write_text("station_id,api_station_id,name,region_id,lat,lon,docks,role\n200,ST-45,example,S05,37,127,15\n")
    with pytest.raises(ValueError, match="row width"):
        load(stations=stations)


def test_cli_exports_reference_geography_only(capsys):
    main(["--regions", str(REGIONS), "--stations", str(STATIONS),
          "--region", "S05"])
    summary = json.loads(capsys.readouterr().out)
    assert summary["stationCount"] == 196 and len(summary["stations"]) == 196


EXPECTED_COUNTS = {"N01": 265, "N02": 175, "N03": 165, "N04": 160, "N05": 145, "N06": 137,
                   "N07": 130, "N08": 99, "S01": 250, "S02": 236, "S03": 226, "S04": 193,
                   "S05": 196, "S06": 134, "S07": 80, "S08": 69, "S09": 56}


def test_all_17_regions_preserve_global_identity_counts_and_source_rows():
    metadata = json.loads((SEEDS / "source.json").read_text())
    regions = load_region_seeds(REGIONS, STATIONS)
    assert list(regions) == metadata["regionIds"] == list(EXPECTED_COUNTS)
    assert {rid: len(seed.stations) for rid, seed in regions.items()} == EXPECTED_COUNTS
    stations = [station for seed in regions.values() for station in seed.stations]
    assert len(stations) == len({s.data_id for s in stations}) == len({s.api_id for s in stations}) == 2716
    for file in metadata["files"]:
        with (ROOT / file["sourcePath"]).open(newline="", encoding="utf-8-sig") as stream:
            assert len(list(csv.DictReader(stream))) == file["rows"]
    assert regions["N04"].id_map(source="data")["102"] == "ST-4"
    assert regions["N01"].depot == (37.582815, 127.026664)
    assert metadata["wireIdField"] == "api_station_id"
    assert not metadata["inventoryIncluded"] and not metadata["roadDataIncluded"]


@pytest.mark.parametrize("region_id", EXPECTED_COUNTS)
def test_each_region_is_selectable_without_changing_the_reference(region_id):
    seed = load(region=region_id)
    assert len(seed.stations) == EXPECTED_COUNTS[region_id]
    assert all(s.region_id == region_id for s in seed.stations)
    assert len(seed.id_map(source="data")) == len(seed.id_map(source="api")) == len(seed.stations)


def test_invalid_other_region_is_not_silently_skipped(tmp_path):
    path = rewrite(STATIONS, tmp_path / "stations.csv", lambda rows: rows[0].__setitem__("lat", "nan"))
    # First station is N04; even an S05 request validates it.
    with pytest.raises(ValueError):
        load(stations=path, region="S05")
    path = rewrite(REGIONS, tmp_path / "regions.csv", lambda rows: rows[0].__setitem__("stations", "264"))
    with pytest.raises(ValueError, match="N01 region station count mismatch"):
        load(regions=path, region="S05")


def test_cross_region_duplicate_api_id_is_rejected(tmp_path):
    def duplicate(rows):
        other = next(row for row in rows if row["region_id"] != rows[0]["region_id"])
        other["api_station_id"] = rows[0]["api_station_id"]
    path = rewrite(STATIONS, tmp_path / "stations.csv", duplicate)
    with pytest.raises(ValueError, match="unique"):
        load(stations=path)


def test_cli_can_validate_all_regions(capsys):
    main(["--regions", str(REGIONS), "--stations", str(STATIONS), "--all-regions"])
    summary = json.loads(capsys.readouterr().out)
    assert summary["referenceOnly"] and summary["regionCount"] == 17 and summary["stationCount"] == 2716



def test_plan_map_demo_reference_matches_n02_source():
    # The demo copies display fields (name, coordinates, docks, elevation, role) from a CSV that is
    # refreshed weekly. Only identity is checked against it so a reference refresh does not break CI.
    import re
    text = (ROOT / "frontend/src/features/plan-map/demo.ts").read_text()
    raw = text.split("const referenceStations = ", 1)[1].split(" as const", 1)[0]
    stations = json.loads(raw)
    with STATIONS.open(newline="", encoding="utf-8") as stream:
        source = {row["api_station_id"]: row for row in csv.DictReader(stream)}
    assert "DEMO_REGION = 'N02'" in text
    assert len(stations) == len({station["stationId"] for station in stations}) == 8
    for station in stations:
        assert source[station["stationId"]]["region_id"] == "N02"
        assert set(station) == {"stationId", "name", "lat", "lon", "docks", "elevationM", "role"}
    # Even unserved entries with no current snapshot must refer to this region.
    for station_id in set(re.findall(r"ST-[0-9]+", text)):
        assert source[station_id]["region_id"] == "N02"
    assert set(re.findall(r"TR-[0-9]+", text)) == {"TR-03", "TR-04", "TR-05"}
    # A fixed pickup station must not be shown as a station that receives bikes.
    roles = {station["stationId"]: station["role"] for station in stations}
    for station_id in re.findall(r"stationId: '(ST-[0-9]+)', action: 'DROPOFF'", text):
        assert roles[station_id] != "FIXED_PICKUP"
