"""Contract validation uses the actual shared YAML, not a copied Python schema."""
import copy
import json
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

from rebalancing.contract import SolveContext, solve_request
from rebalancing.models import Imbalance, RoutingStation, Station, Truck
from rebalancing.osrm import TravelTimeError
from rebalancing.planner import plan_routes
from rebalancing.baseline import plan_greedy
from rebalancing.problem import prepare_problem
from rebalancing.travel_time import HaversineTravelTime

ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = ROOT / "docs/specs/examples/optimizer"
DEMO_TIME = datetime.fromisoformat("2026-10-20T08:00:00+09:00")
RESULT_TIME = datetime.fromisoformat("2026-10-20T08:00:01+09:00")
RESULT_ID = "1d6d7b35-072a-4dd2-b65c-b22c99b5a702"


@pytest.fixture
def request_data():
    return json.loads((EXAMPLES / "request.json").read_text())


@pytest.fixture(scope="module")
def validators():
    registry = Registry()
    for name in ("asyncapi", "openapi"):
        document = yaml.safe_load((ROOT / f"docs/specs/{name}.yaml").read_text())
        document["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        registry = registry.with_resource(f"https://skala.test/{name}.yaml", Resource.from_contents(document))
    return {name: Draft202012Validator({"$ref": f"https://skala.test/asyncapi.yaml#/components/schemas/{name}"},
                                      registry=registry, format_checker=FormatChecker())
            for name in ("SolveRequest", "SolveResult")}


def run(payload, **kwargs):
    return solve_request(payload, travel_time=HaversineTravelTime(),
                         result_time=RESULT_TIME, result_event_id=RESULT_ID, **kwargs)


@pytest.mark.parametrize("policy", ["ortools", "greedy"])
def test_common_wire_schema_and_fixed_ownership(request_data, validators, policy):
    validators["SolveRequest"].validate(request_data)
    result = run(request_data, policy=policy)
    validators["SolveResult"].validate(result)
    assert result["status"] == "OK" and result["unserved"] == []
    assert [route["truckId"] for route in result["routes"]] == ["TR-02"]
    stops = result["routes"][0]["stops"]
    assert [(s["stationId"], s["action"]) for s in stops] == [("ST-10", "PICKUP"), ("ALT-10", "DROPOFF")]
    assert [s["seq"] for s in stops] == [1, 2] and [s["loadAfter"] for s in stops] == [5, 0]
    assert all(datetime.fromisoformat(s["eta"]) >= DEMO_TIME for s in stops)
    assert result["stats"]["bikesMoved"] == 10
    assert result["requestId"] == request_data["requestId"] and result["traceId"] == request_data["traceId"]
    assert result["eventId"] != request_data["eventId"]
    assert set(result) == {"schemaVersion", "shiftId", "traceId", "requestId", "eventId", "status",
                           "routes", "unserved", "stats", "occurredAt"}
    assert "objective" not in result["stats"]


def test_reordered_ids_unknown_fields_and_no_input_mutation(request_data):
    first = run(request_data)
    changed = copy.deepcopy(request_data)
    changed["stations"].reverse()
    changed["trucks"].reverse()
    changed["futureField"] = {"ignored": True}
    changed["params"]["futureParameter"] = 1
    before = copy.deepcopy(changed)
    second = run(changed)
    assert first["routes"] == second["routes"] and first["unserved"] == second["unserved"]
    assert changed == before


def test_blocked_and_partial_result_still_uses_common_ok(request_data, validators):
    request_data["blockedStationIds"] = ["ALT-10"]
    result = run(request_data)
    validators["SolveResult"].validate(result)
    assert result["status"] == "OK"
    assert result["unserved"] == [{"stationId": "ALT-10", "priority": 15.25, "reason": "BLOCKED"}]


def test_zero_demand_is_valid_and_not_a_visit(request_data, validators):
    for station in request_data["stations"]:
        station["neededBikes"] = 0
    result = run(request_data)
    validators["SolveResult"].validate(result)
    assert result["status"] == "OK" and result["routes"] == [] and result["unserved"] == []


@pytest.mark.parametrize("issue", ["duplicate_station", "duplicate_truck", "unknown_fixed", "duplicate_owner",
    "negative_priority", "missing_priority", "nan_priority", "negative_scale", "negative_service", "bad_limit",
    "overloaded_truck", "expired_shift", "naive_shift", "basic_shift", "unknown_blocked", "bad_coordinate", "missing_kind"])
def test_invalid_planning_inputs_return_failed(request_data, validators, issue):
    if issue == "duplicate_station": request_data["stations"].append(request_data["stations"][0])
    elif issue == "duplicate_truck": request_data["trucks"].append(request_data["trucks"][0])
    elif issue == "unknown_fixed": request_data["trucks"][0]["fixedStationIds"] = ["UNKNOWN"]
    elif issue == "duplicate_owner": request_data["trucks"][0]["fixedStationIds"] = ["ST-10"]
    elif issue == "negative_priority": request_data["stations"][0]["priority"] = -1
    elif issue == "missing_priority": del request_data["stations"][0]["priority"]
    elif issue == "nan_priority": request_data["stations"][0]["priority"] = float("nan")
    elif issue == "negative_scale": request_data["params"]["penaltyScale"] = -1
    elif issue == "negative_service": request_data["params"]["serviceMinutesBase"] = -1
    elif issue == "bad_limit": request_data["params"]["solutionLimit"] = 0
    elif issue == "overloaded_truck": request_data["trucks"][0]["load"] = 16
    elif issue == "expired_shift": request_data["trucks"][0]["availableUntil"] = "2026-10-20T07:59:59+09:00"
    elif issue == "naive_shift": request_data["trucks"][0]["availableUntil"] = "2026-10-20T09:00:00"
    elif issue == "basic_shift": request_data["trucks"][0]["availableUntil"] = "20261020T090000+0900"
    elif issue == "unknown_blocked": request_data["blockedStationIds"] = ["UNKNOWN"]
    elif issue == "bad_coordinate": request_data["stations"][0]["lat"] = 127
    elif issue == "missing_kind": del request_data["kind"]
    result = run(request_data)
    validators["SolveResult"].validate(result)
    assert result["status"] == "FAILED" and result["code"] == "INVALID_REQUEST" and result["retryable"] is False


@pytest.mark.parametrize("field,value", [("eventId", "bad"), ("eventId", "09a36711b19a4375aa4632d1cfb53133"), ("schemaVersion", 2),
    ("occurredAt", "20261020T080000+0900"),
    ("schemaVersion", True), ("occurredAt", "2026-10-20"), ("shiftId", ""), ("requestId", None)])
def test_unusable_envelope_cannot_create_a_correlated_result(request_data, field, value):
    request_data[field] = value
    with pytest.raises(ValueError):
        run(request_data)


def test_provider_is_explicit_and_result_identity_is_distinct(request_data):
    with pytest.raises(ValueError):
        SolveContext((37, 127), datetime(2026, 10, 20))
    with pytest.raises(ValueError):
        solve_request(request_data, travel_time=None)
    with pytest.raises(ValueError):
        solve_request(request_data, travel_time=HaversineTravelTime(),
                      result_event_id=request_data["eventId"].upper())


@pytest.mark.parametrize("field,value", [("depot", None), ("depot", [37, 127]),
    ("depot", {"lat": True, "lon": 127}), ("depot", {"lat": 91, "lon": 127}),
    ("depot", {"lat": 37, "lon": 181}), ("asOf", None),
    ("asOf", "2026-10-20T08:00:00"), ("asOf", "20261020T080000+0900")])
def test_invalid_message_context_returns_correlated_input_failure(request_data, validators, field, value):
    if value is None:
        del request_data[field]
    else:
        request_data[field] = value
    result = run(request_data)
    validators["SolveResult"].validate(result)
    assert result["status"] == "FAILED" and result["code"] == "INVALID_REQUEST"
    assert result["retryable"] is False and result["requestId"] == request_data["requestId"]


@pytest.mark.parametrize("code,status", [("budget_exceeded", "TIMEOUT"), ("timeout", "TIMEOUT"),
                                        ("network_error", "FAILED"), ("unreachable", "FAILED"),
                                        ("data_changed", "FAILED"), ("invalid_response", "FAILED"),
                                        ("http_error", "FAILED"), ("osrm_error", "FAILED")])
def test_osrm_errors_use_the_shared_statuses(request_data, validators, code, status):
    provider = HaversineTravelTime()
    with patch.object(HaversineTravelTime, "matrix", side_effect=TravelTimeError(code, "test failure", retryable=True)):
        result = solve_request(request_data, travel_time=provider)
    validators["SolveResult"].validate(result)
    assert result["status"] == status
    assert result["code"] == ("OSRM_REJECTED" if code == "osrm_error" else f"OSRM_{code.upper()}")
    assert result["retryable"] is True and result["error"] == "test failure"


def test_service_rounds_up_shift_rounds_down_and_decimal_penalty(request_data):
    import rebalancing.contract as contract
    request_data["params"].update(serviceMinutesBase=0.001, serviceMinutesPerBike=0.001, penaltyScale=0.2)
    request_data["stations"][0]["priority"] = 0.1
    request_data["trucks"][0]["availableUntil"] = "2026-10-20T08:00:10.999+09:00"
    stations, jobs, trucks, blocked, options, scale, *_ = contract._prepare(request_data, SolveContext.from_request(request_data))
    assert options.base_service_s == 1 and options.per_bike_service_s == 1
    assert trucks[0].available_time_s == 10
    problem = prepare_problem(SolveContext.from_request(request_data).depot, stations, jobs, trucks)
    assert problem.penalty(next(i for i, job in enumerate(problem.imbalances, 1) if job.priority == 0.1), scale) == 1


@pytest.mark.parametrize("policy", [plan_routes, plan_greedy])
def test_fixed_small_truck_does_not_transfer_or_force_infeasible_work(policy):
    depot = (37, 127)
    stations = [RoutingStation(1, "one", 37.001, 127)]
    plan = policy(depot, stations, [Imbalance(1, 5, 10)],
                  [Truck(0, capacity=3, fixed_station_ids=(1,)), Truck(1, capacity=15)],
                  travel_time=HaversineTravelTime())
    assert plan.routes == [] and plan.unserved_reasons == {1: "capacity_exceeded"}


@pytest.mark.parametrize("policy", [plan_routes, plan_greedy])
def test_explicit_snapshot_keeps_inventory_validation(policy):
    with pytest.raises(ValueError, match="inventory"):
        policy((37, 127), [Station(1, "one", 37.001, 127, 10, 1)], [Imbalance(1, 5)], [Truck(0)])


def test_timezone_equivalent_asof_produces_same_eta(request_data):
    first = run(request_data)
    request_data["asOf"] = "2026-10-19T23:00:00+00:00"
    request_data["occurredAt"] = "2026-10-21T08:00:00+09:00"
    assert run(request_data)["routes"] == first["routes"]


def test_saved_examples_match_actual_shared_schemas(request_data, validators):
    result = json.loads((EXAMPLES / "result.json").read_text())
    validators["SolveRequest"].validate(request_data)
    validators["SolveResult"].validate(result)
    computed = run(request_data)
    assert result["routes"] == computed["routes"] and result["unserved"] == computed["unserved"]
    assert result["status"] == computed["status"]


def test_no_solution_uses_failed_without_changing_public_plan_status(request_data, validators):
    from rebalancing.models import Plan
    with patch("rebalancing.contract.plan_routes", return_value=Plan([], [], "ortools", status="no_solution")):
        result = run(request_data)
    validators["SolveResult"].validate(result)
    assert result["status"] == "FAILED" and result["code"] == "NO_SOLUTION" and result["retryable"] is False


@pytest.mark.parametrize("value", [10**400, -10**400, True, "high"])
def test_extreme_priorities_produce_input_failure(request_data, value):
    request_data["stations"][0]["priority"] = value
    assert run(request_data)["status"] == "FAILED"


def test_extreme_coordinate_produces_input_failure(request_data):
    request_data["stations"][0]["lat"] = 10**400
    assert run(request_data)["status"] == "FAILED"


@pytest.mark.parametrize("policy", [plan_routes, plan_greedy])
def test_fixed_assignment_changes_an_otherwise_cheaper_route(policy):
    stations = [RoutingStation(1, "one", 37.001, 127)]
    trucks = [Truck(0, position=(37.001, 127)), Truck(1, position=(37, 127))]
    jobs = [Imbalance(1, 5, 10)]
    free = policy((37, 127), stations, jobs, trucks, travel_time=HaversineTravelTime())
    assert free.routes[0].truck_id == 0
    from dataclasses import replace
    locked = policy((37, 127), stations, jobs, [trucks[0], replace(trucks[1], fixed_station_ids=(1,))],
                    travel_time=HaversineTravelTime())
    assert locked.routes[0].truck_id == 1


def test_no_trucks_returns_common_ok_with_no_trucks_reasons(request_data, validators):
    request_data["trucks"] = []
    with patch.object(HaversineTravelTime, "matrix") as matrix:
        result = run(request_data)
    matrix.assert_not_called()
    validators["SolveResult"].validate(result)
    assert result["status"] == "OK" and result["routes"] == []
    assert {item["reason"] for item in result["unserved"]} == {"NO_TRUCKS"}
    assert len(result["unserved"]) == 2


def test_rfc3339_lowercase_markers_are_accepted(request_data, validators):
    request_data["occurredAt"] = "2026-10-19t23:00:00z"
    for truck in request_data["trucks"]:
        truck["availableUntil"] = "2026-10-20t00:00:00z"
    result = run(request_data)
    validators["SolveResult"].validate(result)
    assert result["status"] == "OK"



@pytest.fixture
def backend89_request():
    """PR #89 SolveInput + SolveGateway envelope; geography comes from ref CSV.

    Inventories/forecasts are deliberately absent: StationNeeds has already
    computed the quantities/priority before the optimizer receives this shape.
    """
    from rebalancing.seeds import REFERENCE_REGIONS, REFERENCE_STATIONS, load_region_seed
    region = load_region_seed(REFERENCE_REGIONS, REFERENCE_STATIONS, "N02")
    first, second, blocked = region.stations[:3]
    return {
        "eventId": "512cb287-6f45-4a78-8f59-56d3a6a76e5a", "schemaVersion": 1,
        "occurredAt": "2026-10-21T06:30:00+09:00", "shiftId": "N02:2026-10-21:AM",
        "requestId": "N02:2026-10-21:AM:0:1", "traceId": "backend89-bridge", "kind": "PLAN",
        "depot": {"lat": region.depot[0], "lon": region.depot[1]}, "asOf": "2026-10-21T07:00:00+09:00",
        "stations": [{"stationId": s.api_id, "lat": s.position[0], "lon": s.position[1],
                      "neededBikes": needed, "priority": priority, "kind": kind}
                     for s, needed, priority, kind in [(first, 3, 3.25, "OVERFLOW"),
                        (second, -3, 6.5, "EMPTY"), (blocked, -3, 12.5, "EMPTY")]],
        "trucks": [{"truckId": tid, "capacity": 15, "load": 0, "lat": region.depot[0],
                    "lon": region.depot[1], "availableUntil": "2026-10-21T10:00:00+09:00"}
                   for tid in ("TR-03", "TR-04")],
        "blockedStationIds": [blocked.api_id],
        "params": {"solutionLimit": 50, "serviceMinutesBase": 3.0, "serviceMinutesPerBike": 0.5},
    }


@pytest.mark.parametrize("policy", ["ortools", "greedy"])
def test_backend89_plan_keeps_computed_demand_priority_and_blocked_station(backend89_request, validators, policy):
    payload = backend89_request
    original = copy.deepcopy(payload)
    validators["SolveRequest"].validate(payload)
    result = run(payload, policy=policy)
    validators["SolveResult"].validate(result)
    assert result["status"] == "OK" and result["traceId"] == payload["traceId"]
    stops = [s for r in result["routes"] for s in r["stops"]]
    assert [(s["stationId"], s["action"], s["bikes"]) for s in stops] == [
        (payload["stations"][0]["stationId"], "PICKUP", 3),
        (payload["stations"][1]["stationId"], "DROPOFF", 3)]
    assert result["unserved"] == [{"stationId": payload["blockedStationIds"][0], "priority": 12.5, "reason": "BLOCKED"}]
    assert all(r["capacity"] == 15 and r["startLoad"] == 0 for r in result["routes"])
    assert payload == original


@pytest.mark.parametrize("policy", ["ortools", "greedy"])
def test_backend89_non_hour_eve_replan_uses_current_load_owner_and_shift_end(backend89_request, validators, policy):
    from rebalancing.contract import _prepare
    payload = backend89_request
    target, blocked = payload["stations"][:2]
    target.update(neededBikes=-4, priority=8.0, kind="EMPTY")
    payload.update(kind="REPLAN", shiftId="N02:2026-10-21:EVE", requestId="N02:2026-10-21:EVE:2:1",
                   occurredAt="2026-10-21T19:37:21+09:00", asOf="2026-10-21T19:37:21+09:00",
                   stations=[target, blocked], blockedStationIds=[blocked["stationId"]])
    for truck in payload["trucks"]:
        truck.update(load=4, lat=target["lat"], lon=target["lon"], availableUntil="2026-10-21T21:00:00+09:00")
    payload["trucks"][0]["fixedStationIds"] = [target["stationId"]]
    validators["SolveRequest"].validate(payload)
    stations, jobs, trucks, blocked_ids, options, scale, *_ = _prepare(payload, SolveContext.from_request(payload))
    assert {t.available_time_s for t in trucks} == {4959}
    assert (options.base_service_s, options.per_bike_service_s, scale) == (180, 30, 3000)
    result = run(payload, policy=policy)
    validators["SolveResult"].validate(result)
    assert result["status"] == "OK" and len(result["routes"]) == 1
    route = result["routes"][0]
    assert (route["truckId"], route["startLoad"]) == ("TR-03", 4)
    assert [(s["stationId"], s["action"], s["bikes"], s["loadAfter"]) for s in route["stops"]] == [
        (target["stationId"], "DROPOFF", 4, 0)]
    assert datetime.fromisoformat(route["stops"][0]["eta"]) == datetime.fromisoformat(payload["asOf"])
    assert result["unserved"][0]["reason"] == "BLOCKED"


@pytest.mark.parametrize("policy", ["ortools", "greedy"])
def test_backend89_empty_candidate_returns_no_routes_for_backend_to_fill(backend89_request, validators, policy):
    payload = backend89_request
    payload.update(stations=[], blockedStationIds=[])
    validators["SolveRequest"].validate(payload)
    with patch.object(HaversineTravelTime, "matrix", side_effect=AssertionError("no work needs no matrix")):
        result = run(payload, policy=policy)
    validators["SolveResult"].validate(result)
    assert result["status"] == "OK" and result["routes"] == result["unserved"] == []
    assert result["stats"]["bikesMoved"] == 0
