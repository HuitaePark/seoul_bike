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
    from rebalancing.kafka_transport import REQUEST_TOPIC, RESULT_TOPIC, create_solve_topics

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
        offsets.assign([TopicPartition(REQUEST_TOPIC, 0, request_start)])
        offsets.commit(offsets=[TopicPartition(REQUEST_TOPIC, 0, request_start)], asynchronous=False)
        observer.assign([TopicPartition(RESULT_TOPIC, 0, result_start)])
        payload = json.loads((ROOT / "docs/specs/examples/optimizer/request.json").read_text())
        payload.update(eventId=str(uuid4()), shiftId=f"live-{unique}:2026-10-20:AM")
        payload["requestId"] = payload["shiftId"] + ":1:1"
        invalid = copy.deepcopy(payload)
        invalid.update(eventId=str(uuid4()), requestId=payload["shiftId"] + ":1:2")
        invalid["trucks"][0]["load"] = 16
        envelope = copy.deepcopy(payload)
        envelope["eventId"] = str(uuid4())
        settings = json.loads((ROOT / "docs/specs/examples/optimizer/context.json").read_text())
        context_file = tmp_path / "contexts.json"
        context_file.write_text(json.dumps({"requests": {
            p["requestId"]: {"shiftId": p["shiftId"], **settings} for p in (payload, invalid)}}))
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
                "--contexts", str(context_file), "--state-dir", str(tmp_path / "state"),
                "--travel-source", "haversine", "--max-messages", str(count),
                "--idle-timeout-seconds", "20"],
                cwd=ROOT / "or-tools", capture_output=True, text=True, timeout=40)
            assert result.returncode == 0, result.stderr

        for p in (payload, payload, envelope, invalid):
            send(p)
        worker(4)
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
        worker(1)
        assert observer.poll(2) is None
        assert offsets.committed([TopicPartition(REQUEST_TOPIC, 0)], timeout=10)[0].offset == request_start + 5
        assert observer.get_watermark_offsets(TopicPartition(RESULT_TOPIC, 0), timeout=10)[1] == result_start + 2
    finally:
        observer.close()
        offsets.close()
