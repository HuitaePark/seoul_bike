"""Sequential solve worker with explicit runtime inputs and durable result reuse."""
from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
import signal
from threading import Event
from time import monotonic

from .contract import _correlation, solve_request
from .journal import FileJournal, canonical
from .kafka_transport import KafkaTransport, REQUEST_TOPIC, RESULT_TOPIC
from .matrix_cache import CachedTravelTime
from .osrm import OsrmTravelTime
from .runtime import Contexts, reject_constant
from .travel_time import HaversineTravelTime

LOG = logging.getLogger(__name__)


def process_message(message, *, transport, journal: FileJournal, contexts: Contexts,
                    travel_time, policy: str = "ortools", solve=solve_request) -> str:
    raw = message.value()
    if not isinstance(raw, bytes):
        raise ValueError("SolveRequest must contain UTF-8 JSON, not a tombstone")
    payload = json.loads(raw.decode("utf-8"), parse_constant=reject_constant)
    _correlation(payload)
    if message.key() != payload["shiftId"].encode("utf-8"):
        raise ValueError("Kafka key must equal SolveRequest.shiftId")
    record = journal.find(payload)
    if record is None:
        context = contexts.resolve(payload)
        result = solve(payload, context=context, travel_time=travel_time, policy=policy)
        headers = [(key, value) for key, value in (message.headers() or [])
                   if key == "traceparent" and value is not None]
        record = journal.prepare(payload, result, headers)
    if record["state"] == "published":
        outcome = "duplicate"
    else:
        transport.publish(key=payload["shiftId"].encode("utf-8"), value=canonical(record["result"]),
                          headers=journal.headers(record))
        journal.mark_published(payload, record)
        outcome = "published"
    transport.commit(message)
    LOG.info("solve %s requestId=%s status=%s", outcome, payload["requestId"], record["result"]["status"])
    return outcome


def run(transport, *, journal, contexts, travel_time, policy="ortools", stop: Event | None = None,
        max_messages: int = 0, idle_timeout_s: float = 0):
    stopping = stop or Event()
    processed = 0
    last_message = monotonic()
    while not stopping.is_set() and (max_messages == 0 or processed < max_messages):
        message = transport.poll(1)
        if message is None:
            if idle_timeout_s and monotonic() - last_message >= idle_timeout_s:
                raise TimeoutError("worker idle timeout; requested message count was not reached")
            continue
        process_message(message, transport=transport, journal=journal, contexts=contexts,
                        travel_time=travel_time, policy=policy)
        processed += 1
        last_message = monotonic()
    return processed


def parser():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--bootstrap-servers", required=True)
    cli.add_argument("--group-id", required=True, help="keep unchanged together with the persistent state directory")
    cli.add_argument("--contexts", type=Path, required=True, help="per-request runtime JSON; never inferred from consumption time")
    cli.add_argument("--state-dir", type=Path, required=True)
    cli.add_argument("--travel-source", choices=("haversine", "osrm"), required=True)
    cli.add_argument("--policy", choices=("ortools", "greedy"), default="ortools")
    cli.add_argument("--osrm-url")
    cli.add_argument("--osrm-data-version")
    cli.add_argument("--matrix-cache-dir", type=Path)
    cli.add_argument("--max-messages", type=int, default=0, help="0 means continuous processing")
    cli.add_argument("--idle-timeout-seconds", type=float, default=0)
    return cli


def main(argv=None) -> int:
    cli = parser()
    args = cli.parse_args(argv)
    if args.max_messages < 0 or not 0 <= args.idle_timeout_seconds <= 86400:
        cli.error("max-messages and idle-timeout-seconds must be non-negative (idle timeout <= 86400)")
    if not args.bootstrap_servers.strip() or not args.group_id.strip():
        cli.error("bootstrap-servers and group-id must be non-empty")
    try:
        contexts = Contexts.load(args.contexts)
        if args.travel_source == "osrm":
            if not args.osrm_url or not args.osrm_data_version or args.osrm_data_version == "unversioned":
                cli.error("OSRM requires an explicit URL and road data version")
            road = OsrmTravelTime(args.osrm_url, data_version=args.osrm_data_version)
            travel = CachedTravelTime(road, namespace=road.cache_namespace, cache_dir=args.matrix_cache_dir)
        else:
            if args.osrm_url or args.osrm_data_version or args.matrix_cache_dir:
                cli.error("OSRM settings cannot be used with the development haversine estimate")
            travel = HaversineTravelTime()
    except (OSError, ValueError) as error:
        cli.error(str(error))
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    stop = Event()
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda _signum, _frame: stop.set())
    identity = json.dumps([args.bootstrap_servers, args.group_id, REQUEST_TOPIC, RESULT_TOPIC])
    try:
        with FileJournal(args.state_dir, identity=identity) as journal:
            transport = KafkaTransport(args.bootstrap_servers, args.group_id)
            try:
                run(transport, journal=journal, contexts=contexts, travel_time=travel, policy=args.policy,
                    stop=stop, max_messages=args.max_messages, idle_timeout_s=args.idle_timeout_seconds)
            finally:
                transport.close()
    except Exception as error:
        LOG.error("worker stopped without acknowledging unfinished input: %s", error)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
