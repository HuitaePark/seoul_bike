"""Recovery tests exercise real files and the actual solve adapter."""
import copy
import json
import logging
from pathlib import Path
from unittest.mock import Mock, patch
from uuid import uuid4

import pytest

from rebalancing.journal import FileJournal, JournalError, canonical
from rebalancing.kafka_transport import KafkaTransport
from rebalancing.matrix_cache import CachedTravelTime
from rebalancing.osrm import TravelTimeError
from rebalancing.runtime import Contexts
from rebalancing.travel_time import HaversineTravelTime
from rebalancing.worker import main, process_message, run

ROOT = Path(__file__).resolve().parents[2]
TRACE = b"00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"


class Message:
    def __init__(self, payload, *, key=None, raw=None):
        self._key = key if key is not None else payload["shiftId"].encode()
        self.raw = canonical(payload) if raw is None else raw

    def key(self):
        return self._key

    def value(self):
        return self.raw

    def headers(self):
        return [("traceparent", TRACE), ("unrelated", b"private")]


class Transport:
    def __init__(self, messages=()):
        self.messages = iter(messages)
        self.sent = []
        self.committed = []
        self.fail_publish = False
        self.fail_commit = False

    def poll(self, _timeout):
        return next(self.messages, None)

    def publish(self, **kwargs):
        if self.fail_publish:
            raise RuntimeError("delivery failed")
        self.sent.append(kwargs)

    def commit(self, message):
        if self.fail_commit:
            raise RuntimeError("commit failed")
        self.committed.append(message)


@pytest.fixture
def payload():
    return json.loads((ROOT / "docs/specs/examples/optimizer/request.json").read_text())


@pytest.fixture
def contexts():
    return Contexts.load(ROOT / "infra/optimizer/runtime.example.json")


def process(payload, transport, journal, contexts, **kwargs):
    return process_message(Message(payload), transport=transport, journal=journal, contexts=contexts,
                           travel_time=HaversineTravelTime(), **kwargs)


def test_success_persists_before_send_and_commits_after_ack(tmp_path, payload, contexts):
    transport = Transport()
    with FileJournal(tmp_path, identity="test") as journal:
        original = transport.publish

        def publish(**kwargs):
            assert journal.find(payload)["state"] == "prepared"
            assert not transport.committed
            original(**kwargs)

        transport.publish = publish
        assert process(payload, transport, journal, contexts) == "published"
        assert journal.find(payload)["state"] == "published"
    sent = transport.sent[0]
    result = json.loads(sent["value"])
    assert sent["key"] == payload["shiftId"].encode()
    assert sent["headers"] == [("traceparent", TRACE)]
    assert result["status"] == "OK" and result["stats"]["bikesMoved"] == 10
    assert result["traceId"] == payload["traceId"] and result["eventId"] != payload["eventId"]
    assert len(transport.committed) == 1


def test_restart_and_new_event_reuse_same_request_without_republishing(tmp_path, payload, contexts):
    transport = Transport()
    with FileJournal(tmp_path, identity="test") as journal:
        process(payload, transport, journal, contexts)
    replacement = copy.deepcopy(payload)
    replacement.update(eventId=str(uuid4()), occurredAt="2026-10-20T08:01:00+09:00", traceId="new-delivery")
    with FileJournal(tmp_path, identity="test") as journal:
        never_solve = Mock(side_effect=AssertionError("must not recalculate"))
        assert process(payload, transport, journal, contexts, solve=never_solve) == "duplicate"
        assert process(replacement, transport, journal, contexts, solve=never_solve) == "duplicate"
    assert len(transport.sent) == 1 and len(transport.committed) == 3


def test_publish_failure_keeps_offset_and_restarts_with_exact_saved_result(tmp_path, payload, contexts):
    transport = Transport()
    transport.fail_publish = True
    with FileJournal(tmp_path, identity="test") as journal:
        with pytest.raises(RuntimeError, match="delivery failed"):
            process(payload, transport, journal, contexts)
        saved = journal.find(payload)
        assert saved["state"] == "prepared" and transport.committed == []
    transport.fail_publish = False
    with FileJournal(tmp_path, identity="test") as journal:
        process(payload, transport, journal, Contexts({"other": {
            "shiftId": "other", "depot": [0, 0], "asOf": "2026-10-20T08:00:00Z"}}),
                solve=Mock(side_effect=AssertionError("must use persisted result")))
    assert json.loads(transport.sent[0]["value"]) == saved["result"]
    assert len(transport.committed) == 1


def test_crash_after_ack_may_resend_but_preserves_event_id(tmp_path, payload, contexts):
    transport = Transport()
    with FileJournal(tmp_path, identity="test") as journal:
        with patch.object(journal, "mark_published", side_effect=OSError("power loss")):
            with pytest.raises(OSError):
                process(payload, transport, journal, contexts)
    assert not transport.committed
    with FileJournal(tmp_path, identity="test") as journal:
        process(payload, transport, journal, contexts)
    assert transport.sent[0] == transport.sent[1]
    assert len(transport.committed) == 1


def test_commit_failure_does_not_republish_after_restart(tmp_path, payload, contexts):
    transport = Transport()
    transport.fail_commit = True
    with FileJournal(tmp_path, identity="test") as journal:
        with pytest.raises(RuntimeError, match="commit failed"):
            process(payload, transport, journal, contexts)
    transport.fail_commit = False
    with FileJournal(tmp_path, identity="test") as journal:
        process(payload, transport, journal, contexts)
    assert len(transport.sent) == 1 and len(transport.committed) == 1


@pytest.mark.parametrize("change", ["event", "request"])
def test_reused_identity_with_changed_input_stops_without_commit(tmp_path, payload, contexts, change):
    transport = Transport()
    with FileJournal(tmp_path, identity="test") as journal:
        process(payload, transport, journal, contexts)
        changed = copy.deepcopy(payload)
        changed["params"]["solutionLimit"] += 1
        if change == "request":
            changed["eventId"] = str(uuid4())
        with pytest.raises(JournalError, match="reused"):
            process(changed, transport, journal, contexts)
    assert len(transport.sent) == 1 and len(transport.committed) == 1


@pytest.mark.parametrize("raw,key", [(b"{", None), (b"\xff", None), (b"null", None),
                                    (b'{"value":NaN}', None), (None, b"wrong-shift")])
def test_invalid_message_stays_uncommitted(tmp_path, payload, contexts, raw, key):
    transport = Transport()
    with FileJournal(tmp_path, identity="test") as journal:
        with pytest.raises(ValueError):
            process_message(Message(payload, raw=raw, key=key), transport=transport, journal=journal,
                            contexts=contexts, travel_time=HaversineTravelTime())
    assert not transport.sent and not transport.committed


def test_unknown_context_is_not_replaced_by_occurrence_or_current_time(tmp_path, payload, contexts):
    payload["requestId"] = "unknown-request"
    transport = Transport()
    with FileJournal(tmp_path, identity="test") as journal:
        with pytest.raises(ValueError, match="explicit runtime context missing"):
            process(payload, transport, journal, contexts)
    assert not transport.sent and not transport.committed


@pytest.mark.parametrize("failure,status", [("invalid", "FAILED"), ("timeout", "TIMEOUT")])
def test_correlated_failure_is_a_normal_published_result(tmp_path, payload, contexts, failure, status):
    transport = Transport()
    if failure == "invalid":
        payload["trucks"][0]["load"] = 16
        travel = HaversineTravelTime()
    else:
        travel = Mock()
        travel.matrix.side_effect = TravelTimeError("timeout", "test timeout", retryable=True)
    with FileJournal(tmp_path, identity="test") as journal:
        process_message(Message(payload), transport=transport, journal=journal, contexts=contexts,
                        travel_time=travel)
    assert json.loads(transport.sent[0]["value"])["status"] == status
    assert len(transport.committed) == 1


def test_disk_failure_before_publish_never_acknowledges_input(tmp_path, payload, contexts):
    transport = Transport()
    with FileJournal(tmp_path, identity="test") as journal:
        with patch("rebalancing.journal.os.replace", side_effect=OSError("disk full")):
            with pytest.raises(JournalError, match="cannot persist"):
                process(payload, transport, journal, contexts)
    assert not transport.sent and not transport.committed


def test_corrupted_state_and_concurrent_process_fail_closed(tmp_path, payload, contexts):
    with FileJournal(tmp_path, identity="test") as journal:
        with pytest.raises(BlockingIOError):
            with FileJournal(tmp_path, identity="test"):
                pass
        process(payload, Transport(), journal, contexts)
    next(tmp_path.glob("request-*.json")).write_text("broken")
    with FileJournal(tmp_path, identity="test") as journal:
        with pytest.raises(JournalError, match="cannot read"):
            process(payload, Transport(), journal, contexts)
    with pytest.raises(JournalError, match="another broker/group"):
        with FileJournal(tmp_path, identity="different-group"):
            pass


def test_finite_worker_counts_duplicates_and_stops(tmp_path, payload, contexts):
    transport = Transport([Message(payload), Message(payload)])
    with FileJournal(tmp_path, identity="test") as journal:
        assert run(transport, journal=journal, contexts=contexts, travel_time=HaversineTravelTime(),
                   max_messages=2) == 2
    assert len(transport.sent) == 1 and len(transport.committed) == 2


@pytest.mark.parametrize("settings", [None, {}, {"shiftId": "shift", "depot": [0, 0]},
                                     {"shiftId": "shift", "depot": [0, 0], "asOf": "2026-10-20T08:00:00"}])
def test_runtime_context_has_no_implicit_defaults(settings):
    with pytest.raises(ValueError):
        Contexts({"request": settings})


def test_cli_rejects_missing_runtime_before_connecting_to_kafka(tmp_path):
    with pytest.raises(SystemExit) as error:
        main(["--bootstrap-servers", "unused:9092", "--group-id", "test", "--contexts", str(tmp_path / "missing"),
              "--state-dir", str(tmp_path / "state"), "--travel-source", "haversine"])
    assert error.value.code == 2 and not (tmp_path / "state").exists()


@pytest.mark.parametrize("pending,delivered", [(1, []), (0, ["delivery error"]), (0, [])])
def test_kafka_transport_does_not_treat_enqueue_as_delivery(pending, delivered):
    transport = KafkaTransport.__new__(KafkaTransport)
    transport.timeout = 1
    transport.producer = Mock()

    def produce(*_args, **kwargs):
        if delivered:
            kwargs["on_delivery"](delivered[0], None)

    transport.producer.produce.side_effect = produce
    transport.producer.flush.return_value = pending
    with pytest.raises(RuntimeError, match="not acknowledged"):
        transport.publish(key=b"shift", value=b"{}", headers=[])


def test_kafka_transport_commits_only_the_completed_message_and_checks_errors():
    transport = KafkaTransport.__new__(KafkaTransport)
    transport.consumer = Mock()
    message = Mock()
    partition = Mock(error=None)
    transport.consumer.commit.return_value = [partition]
    transport.commit(message)
    transport.consumer.commit.assert_called_once_with(message=message, asynchronous=False)
    partition.error = "rebalance"
    with pytest.raises(RuntimeError, match="not committed"):
        transport.commit(message)


def test_valid_json_with_corrupted_result_fails_closed(tmp_path, payload, contexts):
    with FileJournal(tmp_path, identity="test") as journal:
        process(payload, Transport(), journal, contexts)
    path = next(tmp_path.glob("request-*.json"))
    damaged = json.loads(path.read_text())
    damaged["result"]["status"] = "TIMEOUT"
    path.write_text(json.dumps(damaged))
    with FileJournal(tmp_path, identity="test") as journal:
        with pytest.raises(JournalError, match="invalid saved result"):
            process(payload, Transport(), journal, contexts)


def test_lost_journal_identity_does_not_adopt_existing_results(tmp_path, payload, contexts):
    with FileJournal(tmp_path, identity="test") as journal:
        process(payload, Transport(), journal, contexts)
    (tmp_path / "identity.json").unlink()
    with pytest.raises(JournalError, match="identity is missing"):
        with FileJournal(tmp_path, identity="other"):
            pass


def worker_events(caplog):
    return [json.loads(r.getMessage()) for r in caplog.records if r.name == "rebalancing.worker"]


def test_logs_correlate_completion_and_distinguish_cached_result_time(tmp_path, payload, contexts, caplog):
    caplog.set_level(logging.INFO, logger="rebalancing.worker")
    travel = CachedTravelTime(HaversineTravelTime(), namespace="test")
    transport = Transport()
    with FileJournal(tmp_path, identity="test") as journal:
        for _ in range(2):
            process_message(Message(payload), transport=transport, journal=journal, contexts=contexts, travel_time=travel)
    completed = [r for r in worker_events(caplog) if r["event"] == "solve.completed"]
    first, duplicate = completed
    assert first["requestId"] == payload["requestId"] and first["shiftId"] == payload["shiftId"]
    assert first["traceId"] == payload["traceId"] and first["eventId"] == payload["eventId"]
    assert first["resultEventId"] == duplicate["resultEventId"]
    assert first["computed"] is True and first["stage"] == "commit"
    assert first["durationMs"] >= first["computeMs"] >= 0
    assert first["matrixCache"] == {"hits": 0, "misses": 1}
    assert duplicate["outcome"] == "duplicate" and duplicate["computed"] is False and duplicate["computeMs"] == 0
    assert duplicate["matrixCache"] == {"hits": 0, "misses": 0}
    assert first["resultSolveMs"] == duplicate["resultSolveMs"]
    assert len(transport.sent) == 1 and len(transport.committed) == 2
    assert "stations" not in caplog.text and "unrelated" not in caplog.text


@pytest.mark.parametrize("stage,code", [("lookup", "JOURNAL_ERROR"), ("context", "RUNTIME_CONTEXT_ERROR"),
                                       ("compute", "COMPUTATION_ERROR"), ("publish", "PUBLISH_FAILED"),
                                       ("mark_published", "JOURNAL_ERROR"), ("commit", "COMMIT_FAILED")])
def test_failure_log_identifies_stage_without_ack_or_error_body(tmp_path, payload, contexts, caplog, stage, code):
    caplog.set_level(logging.INFO, logger="rebalancing.worker")
    transport = Transport()
    secret = "PRIVATE coordinates=37.5,127 token=hidden"
    failure = Mock(side_effect=RuntimeError(secret))
    kwargs = {}
    with FileJournal(tmp_path, identity="test") as journal:
        if stage == "context":
            contexts = Mock(resolve=failure)
        elif stage == "compute":
            kwargs["solve"] = failure
        elif stage in ("publish", "commit"):
            setattr(transport, stage, failure)
        else:
            setattr(journal, "find" if stage == "lookup" else stage, failure)
        with pytest.raises(RuntimeError, match="PRIVATE"):
            process(payload, transport, journal, contexts, **kwargs)
    error = worker_events(caplog)[-1]
    assert error["event"] == "solve.failed" and error["stage"] == stage and error["errorCode"] == code
    assert error["requestId"] == payload["requestId"] and error["errorType"] == "RuntimeError"
    assert error["durationMs"] >= 0 and not transport.committed
    assert secret not in caplog.text and not any(r["event"] == "solve.completed" for r in worker_events(caplog))


def test_malformed_envelope_log_excludes_raw_input(tmp_path, payload, contexts, caplog):
    transport = Transport()
    with FileJournal(tmp_path, identity="test") as journal:
        with pytest.raises(ValueError):
            process_message(Message(payload, raw=b'{"memo":"PRIVATE_NOTE",broken'), transport=transport,
                            journal=journal, contexts=contexts, travel_time=HaversineTravelTime())
    error = worker_events(caplog)[-1]
    assert error["stage"] == "decode" and error["errorCode"] == "INVALID_ENVELOPE"
    assert "requestId" not in error and "PRIVATE_NOTE" not in caplog.text
    assert not transport.sent and not transport.committed


def test_failed_calculation_is_logged_as_delivered_result_not_worker_failure(tmp_path, payload, contexts, caplog):
    caplog.set_level(logging.INFO, logger="rebalancing.worker")
    payload["trucks"][0]["load"] = 16
    with FileJournal(tmp_path, identity="test") as journal:
        transport = Transport()
        assert process(payload, transport, journal, contexts) == "published"
    completed = worker_events(caplog)[-1]
    assert completed["event"] == "solve.completed" and completed["status"] == "FAILED"
    assert completed["errorCode"] == "INVALID_REQUEST" and len(transport.committed) == 1
