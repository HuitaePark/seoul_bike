"""Reference seed validation protects identity mapping and avoids fabricated state."""
import csv
import hashlib
import json
from pathlib import Path

import pytest

from rebalancing.seeds import load_region_seed, main

ROOT = Path(__file__).resolve().parents[2]
SEEDS = ROOT / "infra/optimizer/seeds"


def load(regions=None, stations=None, region="S05"):
    return load_region_seed(regions or SEEDS / "s05-region.csv", stations or SEEDS / "s05-stations.csv", region)


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
    assert metadata["status"] == "proposal-reference"
    for file in metadata["files"]:
        content = (ROOT / file["exportPath"]).read_bytes()
        assert hashlib.sha256(content).hexdigest() == file["exportSha256"]
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
    ("station_id", ""), ("api_station_id", ""),
])
def test_bad_station_rows_are_rejected(tmp_path, field, value):
    path = rewrite(SEEDS / "s05-stations.csv", tmp_path / "stations.csv",
                   lambda rows: rows[0].__setitem__(field, value))
    with pytest.raises(ValueError):
        load(stations=path)


@pytest.mark.parametrize("field", ["station_id", "api_station_id"])
def test_duplicate_ids_never_collapse_to_one_station(tmp_path, field):
    path = rewrite(SEEDS / "s05-stations.csv", tmp_path / "stations.csv",
                   lambda rows: rows[1].__setitem__(field, rows[0][field]))
    with pytest.raises(ValueError, match="unique"):
        load(stations=path)


@pytest.mark.parametrize("field,value", [("stations", "195"), ("depot_lat", "91"), ("trucks_min", "0")])
def test_bad_region_metadata_is_rejected(tmp_path, field, value):
    path = rewrite(SEEDS / "s05-region.csv", tmp_path / "region.csv",
                   lambda rows: rows[0].__setitem__(field, value))
    with pytest.raises(ValueError):
        load(regions=path)


def test_unknown_region_and_missing_columns_are_reported(tmp_path):
    with pytest.raises(ValueError, match="unknown region_id"):
        load(region="N01")
    path = tmp_path / "stations.csv"
    path.write_text("station_id,name\n200,test\n")
    with pytest.raises(ValueError, match="columns"):
        load(stations=path)


def test_duplicate_region_and_inconsistent_row_width_are_rejected(tmp_path):
    regions = rewrite(SEEDS / "s05-region.csv", tmp_path / "regions.csv",
                      lambda rows: rows.append(dict(rows[0])))
    with pytest.raises(ValueError, match="duplicate region"):
        load(regions=regions)
    stations = tmp_path / "stations.csv"
    stations.write_text("station_id,api_station_id,name,region_id,lat,lon,docks,role\n200,ST-45,example,S05,37,127,15\n")
    with pytest.raises(ValueError, match="row width"):
        load(stations=stations)


def test_cli_exports_reference_geography_only(capsys):
    main(["--regions", str(SEEDS / "s05-region.csv"), "--stations", str(SEEDS / "s05-stations.csv"),
          "--region", "S05"])
    summary = json.loads(capsys.readouterr().out)
    assert summary["stationCount"] == 196 and len(summary["stations"]) == 196
