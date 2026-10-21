"""Recovery must hand off durably, survive restart, and never quarantine lost state."""
import copy
import json
from threading import Event
from unittest.mock import Mock, patch
from uuid import uuid4

import pytest

from rebalancing.journal import FileJournal, JournalError
from rebalancing.kafka_transport import DLQ_TOPIC, REQUEST_TOPIC, RETRY_TOPIC, KafkaTransport
from rebalancing.recovery import ATTEMPT, ERROR, OFFSET, ORIGINAL, PARTITION, STAGE, Recovery
from rebalancing.runtime import Contexts
from rebalancing.osrm import TravelTimeError
from rebalancing.travel_time import HaversineTravelTime
from rebalancing.worker import main, process_message, run
from tests.test_worker import Message, Transport, TRACE, contexts, payload


class DeliveryMessage(Message):
    def __init__(self, payload, *, topic=REQUEST_TOPIC, offset=12, headers=None, **kwargs):
        super().__init__(payload, **kwargs)
        self._topic, self._offset, self._headers = topic, offset, headers

    def topic(self):
        return self._topic

    def offset(self):
        return self._offset

    def headers(self):
        return super().headers() if self._headers is None else self._headers


class RecoveryTransport(Transport):
    def __init__(self, messages=()):
        super().__init__(messages)
        self.forwarded = []
        self.fail_forward = False

    def forward(self, **kwargs):
        assert len(self.committed) == len(self.sent) + len(self.forwarded)
        if self.fail_forward:
            raise RuntimeError("forward not acknowledged")
        self.forwarded.append(kwargs)


def consume(tmp_path, transport, contexts, **kwargs):
    with FileJournal(tmp_path, identity="recovery") as journal:
        return run(transport, journal=journal, contexts=contexts, travel_time=HaversineTravelTime(),
                   retry_delay_s=0, **kwargs)


def replay(payload, forwarded):
    return DeliveryMessage(payload, topic=forwarded["topic"], raw=forwarded["value"],
                           key=forwarded["key"], headers=forwarded["headers"], offset=25)


@pytest.mark.parametrize("raw", [b"{PRIVATE", b"\xff", b"null", b'{"x":NaN}', None])
def test_poison_does_not_block_following_good_request(tmp_path, payload, contexts, raw, caplog):
    broken = DeliveryMessage(payload)
    broken.raw = raw  # Includes a real Kafka tombstone.
    transport = RecoveryTransport([broken, DeliveryMessage(payload)])
    assert consume(tmp_path, transport, contexts, max_messages=2) == 2
    routed, = transport.forwarded
    assert routed["topic"] == DLQ_TOPIC and routed["value"] == raw
    assert routed["key"] == payload["shiftId"].encode()
    headers = dict(routed["headers"])
    assert headers["traceparent"] == TRACE and headers["unrelated"] == b"private"
    assert headers[ORIGINAL] == REQUEST_TOPIC.encode() and headers[OFFSET] == b"12"
    assert headers[ATTEMPT] == b"0" and headers[ERROR] in (b"INVALID_ENVELOPE", b"INVALID_REQUEST")
    assert len(transport.sent) == 1 and len(transport.committed) == 2
    assert "PRIVATE" not in caplog.text


def test_context_retry_bound_survives_each_worker_restart(tmp_path, payload, contexts):
    payload["requestId"] = "missing-runtime"
    message = DeliveryMessage(payload, headers=[("traceparent", TRACE), (ATTEMPT, b"0"), (OFFSET, b"forged")])
    for expected in (1, 2, 3, 3):
        transport = RecoveryTransport([message])
        assert consume(tmp_path, transport, contexts, max_messages=1) == 1
        routed, = transport.forwarded
        headers = dict(routed["headers"])
        assert headers[ATTEMPT] == str(expected).encode()
        assert headers[OFFSET] == b"12" and headers[PARTITION] == b"0"
        assert headers[ORIGINAL] == REQUEST_TOPIC.encode() and headers[STAGE] == b"context"
        assert headers[ERROR] == b"RUNTIME_CONTEXT_ERROR"
        assert routed["value"] == message.value() and routed["key"] == message.key()
        assert not transport.sent and transport.committed == [message]
        if routed["topic"] == DLQ_TOPIC:
            break
        message = replay(payload, routed)
    else:
        pytest.fail("retry chain never reached DLQ")
    assert expected == 3


def test_retry_can_succeed_when_runtime_is_supplied_on_restart(tmp_path, payload, contexts):
    other = Contexts({"other": {"shiftId": "other", "depot": [0, 0], "asOf": "2026-10-20T08:00:00Z"}})
    first = RecoveryTransport([DeliveryMessage(payload)])
    consume(tmp_path, first, other, max_messages=1)
    second = RecoveryTransport([replay(payload, first.forwarded[0])])
    consume(tmp_path, second, contexts, max_messages=1)
    assert not second.forwarded and json.loads(second.sent[0]["value"])["status"] == "OK"


def test_zero_retry_budget_sends_missing_context_directly_to_dlq(tmp_path, payload, contexts):
    payload["requestId"] = "missing-runtime"
    transport = RecoveryTransport([DeliveryMessage(payload)])
    consume(tmp_path, transport, contexts, max_messages=1, retry_attempts=0)
    assert transport.forwarded[0]["topic"] == DLQ_TOPIC
    assert dict(transport.forwarded[0]["headers"])[ATTEMPT] == b"0"


def test_wrong_shift_key_is_quarantined_without_running_solver(tmp_path, payload, contexts):
    message = DeliveryMessage(payload, key=b"wrong-shift")
    transport = RecoveryTransport([message])
    consume(tmp_path, transport, contexts, max_messages=1)
    assert transport.forwarded[0]["key"] == b"wrong-shift" and not transport.sent
    assert dict(transport.forwarded[0]["headers"])[ERROR] == b"INVALID_REQUEST"


@pytest.mark.parametrize("status", ["FAILED", "TIMEOUT"])
def test_normal_failure_results_are_not_retried_by_recovery(tmp_path, payload, contexts, status):
    travel = HaversineTravelTime()
    if status == "FAILED":
        payload["trucks"][0]["load"] = 16
    else:
        travel = Mock()
        travel.matrix.side_effect = TravelTimeError("timeout", "test", retryable=True)
    transport = RecoveryTransport([DeliveryMessage(payload)])
    with FileJournal(tmp_path, identity="recovery") as journal:
        run(transport, journal=journal, contexts=contexts, travel_time=travel, max_messages=1)
    assert json.loads(transport.sent[0]["value"])["status"] == status
    assert not transport.forwarded and len(transport.committed) == 1


def test_publish_retry_reuses_exact_saved_result_without_computation(tmp_path, payload, contexts):
    message = DeliveryMessage(payload)
    first = RecoveryTransport([message])
    first.fail_publish = True
    consume(tmp_path, first, contexts, max_messages=1)
    with FileJournal(tmp_path, identity="recovery") as journal:
        saved = journal.find(payload)["result"]
    second = RecoveryTransport([replay(payload, first.forwarded[0])])
    with FileJournal(tmp_path, identity="recovery") as journal:
        process_message(next(second.messages), transport=second, journal=journal, contexts=contexts,
                        travel_time=HaversineTravelTime(), solve=Mock(side_effect=AssertionError("no solve")))
    assert json.loads(second.sent[0]["value"]) == saved


def test_unexpected_computation_error_retries_without_logging_body(tmp_path, payload, contexts, caplog):
    message = DeliveryMessage(payload)
    transport = RecoveryTransport()
    recovery = Recovery(transport)
    with FileJournal(tmp_path, identity="recovery") as journal:
        assert process_message(message, transport=transport, journal=journal, contexts=contexts,
                               travel_time=HaversineTravelTime(), recovery=recovery,
                               delivery=recovery.inspect(message),
                               solve=Mock(side_effect=RuntimeError("SECRET coordinates"))) == "retry"
    assert transport.forwarded[0]["topic"] == RETRY_TOPIC
    assert dict(transport.forwarded[0]["headers"])[ERROR] == b"COMPUTATION_ERROR"
    assert "SECRET" not in caplog.text and not transport.sent


@pytest.mark.parametrize("mutate", [
    lambda h: [(k, b"0" if k == ATTEMPT else v) for k, v in h],
    lambda h: [(k, b"4" if k == ATTEMPT else v) for k, v in h],
    lambda h: [(k, b"-1" if k == OFFSET else v) for k, v in h],
    lambda h: [(k, b"other" if k == ORIGINAL else v) for k, v in h],
    lambda h: h + [(ATTEMPT, b"1")],
    lambda h: [(k, None if k == PARTITION else v) for k, v in h],
    lambda h: [],
])
def test_invalid_retry_metadata_goes_to_dlq_without_resetting_chain(tmp_path, payload, contexts, mutate):
    headers = [(ORIGINAL, REQUEST_TOPIC.encode()), (ATTEMPT, b"1"), (PARTITION, b"0"), (OFFSET, b"12")]
    message = DeliveryMessage(payload, topic=RETRY_TOPIC, headers=mutate(headers))
    transport = RecoveryTransport([message])
    consume(tmp_path, transport, contexts, max_messages=1)
    assert not transport.sent and transport.forwarded[0]["topic"] == DLQ_TOPIC
    assert dict(transport.forwarded[0]["headers"])[ERROR] == b"INVALID_RETRY_METADATA"
    assert transport.committed == [message]


@pytest.mark.parametrize("failure", ["forward", "commit"])
def test_handoff_failure_stops_before_later_input(tmp_path, payload, contexts, failure):
    transport = RecoveryTransport([DeliveryMessage(payload, raw=b"{"), DeliveryMessage(payload)])
    setattr(transport, "fail_" + failure, True)
    with pytest.raises(RuntimeError):
        consume(tmp_path, transport, contexts, max_messages=2)
    assert not transport.committed and not transport.sent
    assert len(transport.forwarded) == (failure == "commit")


@pytest.mark.parametrize("identity", ["event", "request"])
def test_changed_identity_is_quarantined_and_good_duplicate_continues(tmp_path, payload, contexts, identity):
    first = RecoveryTransport([DeliveryMessage(payload)])
    consume(tmp_path, first, contexts, max_messages=1)
    changed = copy.deepcopy(payload)
    changed["params"]["solutionLimit"] += 1
    if identity == "request":
        changed["eventId"] = str(uuid4())
    second = RecoveryTransport([DeliveryMessage(changed), DeliveryMessage(payload)])
    consume(tmp_path, second, contexts, max_messages=2)
    assert second.forwarded[0]["topic"] == DLQ_TOPIC and not second.sent
    assert len(second.committed) == 2


@pytest.mark.parametrize("damage", ["disk", "request", "event", "mark_published", "commit"])
def test_state_or_commit_failure_is_never_quarantined(tmp_path, payload, contexts, damage):
    transport = RecoveryTransport([DeliveryMessage(payload), DeliveryMessage(payload)])
    with FileJournal(tmp_path, identity="recovery") as journal:
        if damage in ("request", "event"):
            process_message(DeliveryMessage(payload), transport=RecoveryTransport(), journal=journal,
                            contexts=contexts, travel_time=HaversineTravelTime())
            path = next(tmp_path.glob(damage + "-*.json"))
            record = json.loads(path.read_text())
            record["fingerprint"] = None
            path.write_text(json.dumps(record))
        if damage == "commit":
            transport.fail_commit = True
        target = "_write" if damage == "disk" else "mark_published"
        if damage in ("disk", "mark_published"):
            setattr(journal, target, Mock(side_effect=JournalError("storage lost")))
        with pytest.raises((JournalError, RuntimeError)):
            run(transport, journal=journal, contexts=contexts, travel_time=HaversineTravelTime(), max_messages=2)
    assert not transport.forwarded and not transport.committed


def test_shutdown_during_retry_wait_leaves_input_uncommitted(tmp_path, payload, contexts):
    headers = [(ORIGINAL, REQUEST_TOPIC.encode()), (ATTEMPT, b"1"), (PARTITION, b"0"), (OFFSET, b"12")]
    transport = RecoveryTransport([DeliveryMessage(payload, topic=RETRY_TOPIC, headers=headers)])
    stop = Event()

    def shutdown(_delay):
        stop.set()
        return True

    with patch.object(stop, "wait", side_effect=shutdown):
        assert consume(tmp_path, transport, contexts, stop=stop, max_messages=1) == 0
    assert not transport.sent and not transport.committed and not transport.forwarded


@pytest.mark.parametrize("flag,value", [("--retry-attempts", "-1"), ("--retry-attempts", "11"),
                                      ("--retry-delay-seconds", "nan"), ("--retry-delay-seconds", "inf"),
                                      ("--retry-delay-seconds", "61"), ("--retry-delay-seconds", "-1")])
def test_cli_rejects_invalid_recovery_before_opening_state(tmp_path, flag, value):
    with pytest.raises(SystemExit) as error:
        main(["--bootstrap-servers", "unused", "--group-id", "test", "--contexts", "unused",
              "--state-dir", str(tmp_path), "--travel-source", "haversine", flag, value])
    assert error.value.code == 2 and not list(tmp_path.iterdir())


def test_kafka_subscribes_to_requests_and_retries_without_auto_creation():
    with patch("confluent_kafka.Consumer") as consumer, patch("confluent_kafka.Producer"):
        KafkaTransport("test", "group")
    config = consumer.call_args.args[0]
    assert config["enable.auto.commit"] is False and config["enable.auto.offset.store"] is False
    assert config["allow.auto.create.topics"] is False
    consumer.return_value.subscribe.assert_called_once_with([REQUEST_TOPIC, RETRY_TOPIC])


@pytest.mark.parametrize("topic", [RETRY_TOPIC, DLQ_TOPIC])
def test_recovery_publish_requires_ack_even_when_flush_returns_zero(topic):
    transport = KafkaTransport.__new__(KafkaTransport)
    transport.timeout, transport.producer = 1, Mock()
    transport.producer.flush.return_value = 0
    with pytest.raises(RuntimeError, match="not acknowledged"):
        transport.forward(topic=topic, key=None, value=None, headers=[])
