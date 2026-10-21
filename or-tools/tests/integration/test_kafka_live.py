"""Opt-in real-broker test. Use only the independent optimizer development broker."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
from time import monotonic
from uuid import uuid4

import pytest

ROOT = Path(__file__).resolve().parents[3]
BOOTSTRAP = os.environ.get("OPTIMIZER_KAFKA_BOOTSTRAP")
pytestmark = pytest.mark.skipif(not BOOTSTRAP, reason="set OPTIMIZER_KAFKA_BOOTSTRAP for the isolated broker")


def test_real_kafka_worker_restarts_and_deduplicates(tmp_path):
    from confluent_kafka import Consumer, Producer, TopicPartition
    import yaml
    from jsonschema import Draft202012Validator, FormatChecker
    from referencing import Registry, Resource
    from rebalancing.kafka_transport import REQUEST_TOPIC, RESULT_TOPIC, RETRY_TOPIC, create_solve_topics

    create_solve_topics(BOOTSTRAP)
    create_solve_topics(BOOTSTRAP)  # Initializer must be safe to run again.
    unique = uuid4().hex
    group = f"optimizer-test-{unique}"
    observer = Consumer({"bootstrap.servers": BOOTSTRAP, "group.id": f"observer-{unique}",
                         "enable.auto.commit": False, "enable.auto.offset.store": False})
    offsets = Consumer({"bootstrap.servers": BOOTSTRAP, "group.id": group,
                        "enable.auto.commit": False, "enable.auto.offset.store": False})
    producer = Producer({"bootstrap.servers": BOOTSTRAP, "acks": "all"})
    try:
        request_start = offsets.get_watermark_offsets(TopicPartition(REQUEST_TOPIC, 0), timeout=10)[1]
        result_start = observer.get_watermark_offsets(TopicPartition(RESULT_TOPIC, 0), timeout=10)[1]
        # Skip previous smoke-test data in this dedicated development topic.
        retry_start = offsets.get_watermark_offsets(TopicPartition(RETRY_TOPIC, 0), timeout=10)[1]
        starts = [TopicPartition(REQUEST_TOPIC, 0, request_start), TopicPartition(RETRY_TOPIC, 0, retry_start)]
        offsets.assign(starts)
        offsets.commit(offsets=starts, asynchronous=False)
        observer.assign([TopicPartition(RESULT_TOPIC, 0, result_start)])
        payload = json.loads((ROOT / "docs/specs/examples/optimizer/request.json").read_text())
        payload.update(eventId=str(uuid4()), shiftId=f"live-{unique}:2026-10-20:AM")
        payload["requestId"] = payload["shiftId"] + ":1:1"
        invalid = copy.deepcopy(payload)
        invalid.update(eventId=str(uuid4()), requestId=payload["shiftId"] + ":1:2")
        invalid["trucks"][0]["load"] = 16
        envelope = copy.deepcopy(payload)
        envelope["eventId"] = str(uuid4())
        trace = b"00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"

        def send(p):
            errors = []
            producer.produce(REQUEST_TOPIC, partition=0, key=p["shiftId"].encode(),
                             value=json.dumps(p).encode(), headers=[("traceparent", trace)],
                             on_delivery=lambda error, _message: errors.append(error))
            assert producer.flush(10) == 0 and errors == [None]

        def worker(count):
            result = subprocess.run([
                sys.executable, "-B", "-m", "rebalancing.worker",
                "--bootstrap-servers", BOOTSTRAP, "--group-id", group,
                "--state-dir", str(tmp_path / "state"),
                "--travel-source", "haversine", "--max-messages", str(count),
                "--idle-timeout-seconds", "20"],
                cwd=ROOT / "or-tools", capture_output=True, text=True, timeout=40)
            assert result.returncode == 0, result.stderr
            logs = []
            for line in result.stderr.splitlines():
                if line.startswith('{"event":'):
                    logs.append(json.loads(line))
            completed = [r for r in logs if r["event"] == "solve.completed"]
            assert len(completed) == count, result.stderr
            assert all(r["shiftId"] == payload["shiftId"] and r["durationMs"] >= 0 for r in completed)
            assert logs[0]["event"] == "worker.started" and logs[-1]["outcome"] == "clean"
            return completed

        for p in (payload, payload, envelope, invalid):
            send(p)
        completed = worker(4)
        assert [r["computed"] for r in completed] == [True, False, False, True]
        received = []
        deadline = monotonic() + 10
        while len(received) < 2 and monotonic() < deadline:
            message = observer.poll(1)
            if message is not None:
                assert not message.error(), message.error()
                assert message.key() == payload["shiftId"].encode()
                assert message.headers() == [("traceparent", trace)]
                received.append(json.loads(message.value()))
        assert len(received) == 2
        registry = Registry()
        for name in ("asyncapi", "openapi"):
            document = yaml.safe_load((ROOT / f"docs/specs/{name}.yaml").read_text())
            document["$schema"] = "https://json-schema.org/draft/2020-12/schema"
            registry = registry.with_resource(f"https://skala.test/{name}.yaml", Resource.from_contents(document))
        validator = Draft202012Validator({"$ref": "https://skala.test/asyncapi.yaml#/components/schemas/SolveResult"},
                                        registry=registry, format_checker=FormatChecker())
        for result in received:
            validator.validate(result)
        assert [(r["requestId"], r["status"]) for r in received] == [
            (payload["requestId"], "OK"), (invalid["requestId"], "FAILED")]
        assert received[0]["traceId"] == payload["traceId"]
        assert received[0]["stats"]["bikesMoved"] == 10
        # A new process and a new input offset must still reuse the completed request.
        send(envelope)
        assert worker(1)[0]["outcome"] == "duplicate"
        assert observer.poll(2) is None
        assert offsets.committed([TopicPartition(REQUEST_TOPIC, 0)], timeout=10)[0].offset == request_start + 5
        assert observer.get_watermark_offsets(TopicPartition(RESULT_TOPIC, 0), timeout=10)[1] == result_start + 2
    finally:
        observer.close()
        offsets.close()


def test_real_kafka_poison_and_bounded_retries_survive_restart(tmp_path):
    from confluent_kafka import Consumer, Producer, TopicPartition
    from rebalancing.kafka_transport import REQUEST_TOPIC, RESULT_TOPIC, RETRY_TOPIC, DLQ_TOPIC, create_solve_topics
    from rebalancing.recovery import ATTEMPT, ERROR, OFFSET, ORIGINAL

    create_solve_topics(BOOTSTRAP)
    unique = uuid4().hex
    group = f"recovery-test-{unique}"
    observer = Consumer({"bootstrap.servers": BOOTSTRAP, "group.id": f"observer-{unique}",
                         "enable.auto.commit": False, "enable.auto.offset.store": False})
    offsets = Consumer({"bootstrap.servers": BOOTSTRAP, "group.id": group,
                        "enable.auto.commit": False, "enable.auto.offset.store": False})
    producer = Producer({"bootstrap.servers": BOOTSTRAP, "acks": "all"})
    try:
        starts = {topic: observer.get_watermark_offsets(TopicPartition(topic, 0), timeout=10)[1]
                  for topic in (REQUEST_TOPIC, RESULT_TOPIC, RETRY_TOPIC, DLQ_TOPIC)}
        inputs = [TopicPartition(topic, 0, starts[topic]) for topic in (REQUEST_TOPIC, RETRY_TOPIC)]
        offsets.assign(inputs)
        offsets.commit(offsets=inputs, asynchronous=False)
        observer.assign([TopicPartition(topic, 0, starts[topic])
                         for topic in (RESULT_TOPIC, RETRY_TOPIC, DLQ_TOPIC)])
        payload = json.loads((ROOT / "docs/specs/examples/optimizer/request.json").read_text())
        payload.update(eventId=str(uuid4()), shiftId=f"recovery-{unique}:2026-10-20:AM", requestId=f"good-{unique}")
        retrying = {**payload, "eventId": str(uuid4()), "requestId": f"retrying-{unique}"}
        trace = b"00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
        raw_retrying = json.dumps(retrying).encode()
        raw_bad = b'{"PRIVATE":broken'
        for raw in (raw_bad, json.dumps(payload).encode(), raw_retrying):
            delivered = []
            producer.produce(REQUEST_TOPIC, partition=0, key=payload["shiftId"].encode(), value=raw,
                             headers=[("traceparent", trace)],
                             on_delivery=lambda error, _message: delivered.append(error))
            assert producer.flush(10) == 0 and delivered == [None]

        # Inject one deterministic result-publication failure. Retry/DLQ records
        # and source commits still go through the real Kafka broker.
        worker_script = """
import json, sys
from rebalancing import worker
publish = worker.KafkaTransport.publish
request_id = sys.argv[1]
def unavailable(self, **kwargs):
    if json.loads(kwargs["value"])["requestId"] == request_id:
        raise RuntimeError("test result publication unavailable")
    return publish(self, **kwargs)
worker.KafkaTransport.publish = unavailable
raise SystemExit(worker.main(sys.argv[2:]))
"""
        routed = []
        for count in (3, 1, 2):
            result = subprocess.run([
                sys.executable, "-B", "-c", worker_script, retrying["requestId"],
                "--bootstrap-servers", BOOTSTRAP, "--group-id", group,
                "--state-dir", str(tmp_path / "state"),
                "--travel-source", "haversine", "--max-messages", str(count),
                "--idle-timeout-seconds", "20", "--retry-delay-seconds", "0.01"],
                cwd=ROOT / "or-tools", capture_output=True, text=True, timeout=40)
            assert result.returncode == 0, result.stderr
            logs = [json.loads(line) for line in result.stderr.splitlines() if line.startswith('{"event":')]
            routed.extend(r for r in logs if r["event"] == "solve.routed")
            assert logs[-1]["outcome"] == "clean"
            assert "PRIVATE" not in result.stderr
        assert [r["attempt"] for r in routed if r["outcome"] == "retry"] == [1, 2, 3]
        received = []
        deadline = monotonic() + 10
        while len(received) < 6 and monotonic() < deadline:
            message = observer.poll(1)
            if message is not None:
                assert not message.error(), message.error()
                received.append(message)
        assert len(received) == 6
        results = [m for m in received if m.topic() == RESULT_TOPIC]
        retries = [m for m in received if m.topic() == RETRY_TOPIC]
        dead = [m for m in received if m.topic() == DLQ_TOPIC]
        assert len(results) == 1 and json.loads(results[0].value())["requestId"] == payload["requestId"]
        assert json.loads(results[0].value())["status"] == "OK"
        assert [dict(m.headers())[ATTEMPT] for m in retries] == [b"1", b"2", b"3"]
        assert len(dead) == 2 and {m.value() for m in dead} == {raw_bad, raw_retrying}
        for message in retries + dead:
            headers = dict(message.headers())
            assert message.key() == payload["shiftId"].encode() and headers["traceparent"] == trace
            assert headers[ORIGINAL] == REQUEST_TOPIC.encode()
            if message.value() == raw_retrying:
                assert headers[OFFSET] == str(starts[REQUEST_TOPIC] + 2).encode()
                assert headers[ERROR] == b"PUBLISH_FAILED"
            else:
                assert headers[OFFSET] == str(starts[REQUEST_TOPIC]).encode()
                assert headers[ERROR] == b"INVALID_ENVELOPE"
        committed = offsets.committed([TopicPartition(REQUEST_TOPIC, 0), TopicPartition(RETRY_TOPIC, 0)], timeout=10)
        assert {p.topic: p.offset for p in committed} == {
            REQUEST_TOPIC: starts[REQUEST_TOPIC] + 3, RETRY_TOPIC: starts[RETRY_TOPIC] + 3}
    finally:
        observer.close()
        offsets.close()


def test_real_kafka_outputs_match_java_partitions_and_keep_key_order(monkeypatch):
    from confluent_kafka import Consumer, TopicPartition
    from confluent_kafka.admin import AdminClient, NewTopic
    from rebalancing import kafka_transport

    # Oracle: Kafka Java 4.2.1 Utils.toPositive(Utils.murmur2(UTF_8(key))) % 6.
    # Generated with the backend's Java client, not a second Python hash implementation.
    expected = {
        "S01:2026-10-21:AM": 0, "N03:2026-10-21:PM": 1,
        "N06:2026-10-21:EVE": 2, "S05:2026-10-21:AM": 3,
        "S03:2026-10-21:AM": 4, "S04:2026-10-21:AM": 5,
        "서울:2026-10-21:AM": 0,
    }
    unique = uuid4().hex
    names = {name: f"partition-test-{unique}-{suffix}" for name, suffix in (
        ("REQUEST_TOPIC", "request"), ("RESULT_TOPIC", "result"),
        ("RETRY_TOPIC", "retry"), ("DLQ_TOPIC", "dlq"))}
    admin = AdminClient({"bootstrap.servers": BOOTSTRAP})
    for future in admin.create_topics([NewTopic(t, num_partitions=6, replication_factor=1)
                                      for t in names.values()], request_timeout=10).values():
        future.result(timeout=15)
    transport = observer = None
    try:
        for name, topic in names.items():
            monkeypatch.setattr(kafka_transport, name, topic)
        transport = kafka_transport.KafkaTransport(BOOTSTRAP, f"partition-worker-{unique}")
        observer = Consumer({"bootstrap.servers": BOOTSTRAP, "group.id": f"partition-observer-{unique}",
                             "enable.auto.commit": False, "enable.auto.offset.store": False})
        outputs = [names[n] for n in ("RESULT_TOPIC", "RETRY_TOPIC", "DLQ_TOPIC")]
        observer.assign([TopicPartition(t, p, 0) for t in outputs for p in range(6)])
        for seq in range(2):
            for key in expected:
                for topic in outputs:
                    transport.forward(topic=topic, key=key.encode(), value=str(seq).encode(),
                                      headers=[("traceparent", b"partition-probe")])
        received = {}
        count, target = 0, len(expected) * len(outputs) * 2
        deadline = monotonic() + 15
        while count < target and monotonic() < deadline:
            message = observer.poll(1)
            if message is None:
                continue
            assert not message.error(), message.error()
            key = message.key().decode()
            assert message.partition() == expected[key], (key, message.topic(), message.partition())
            assert message.headers() == [("traceparent", b"partition-probe")]
            received.setdefault((message.topic(), key), []).append(message.value())
            count += 1
        assert count == target
        assert all(received[(t, key)] == [b"0", b"1"] for t in outputs for key in expected)
    finally:
        if observer is not None:
            observer.close()
        if transport is not None:
            transport.close()
        # Delete only the unique topics this test created, never the shared development topics.
        for future in admin.delete_topics(list(names.values()), request_timeout=10).values():
            future.result(timeout=15)
