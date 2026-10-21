"""Single-worker durable result journal; never accesses the business database."""
from __future__ import annotations

import base64
import fcntl
import hashlib
import json
import os
from pathlib import Path
import tempfile

from .contract import FAILURE_CODES, UNSERVED_REASONS


def canonical(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def digest(value) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def request_fingerprint(payload: dict) -> str:
    # A new delivery may have a new envelope, but a requestId cannot change its work.
    return digest({key: value for key, value in payload.items()
                   if key not in ("eventId", "occurredAt", "traceId")})


class JournalError(RuntimeError):
    pass


class RequestConflict(JournalError):
    """A valid saved identity was reused with different request content."""


class FileJournal:
    """One process, one persistent directory, one broker/group identity.

    No expiry: deleting old entries would allow old requestIds to run again.
    A lost disk or another independent journal is NOT a distributed dedup store.
    """

    def __init__(self, directory: str | Path, *, identity: str):
        self.directory = Path(directory)
        self.identity = identity
        self._lock = None

    def __enter__(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        self._lock = (self.directory / ".lock").open("a+b")
        try:
            fcntl.flock(self._lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            path = self.directory / "identity.json"
            metadata = self._read(path)
            if metadata is None:
                if any(self.directory.glob("event-*.json")) or any(self.directory.glob("request-*.json")):
                    raise JournalError("journal identity is missing while result state exists; restore metadata")
                self._write(path, {"version": 1, "identity": self.identity})
            elif metadata != {"version": 1, "identity": self.identity}:
                raise JournalError("journal belongs to another broker/group; reuse the original identity")
        except Exception:
            self.__exit__(None, None, None)
            raise
        return self

    def __exit__(self, *_):
        if self._lock is not None:
            self._lock.close()
            self._lock = None

    def _path(self, kind: str, identifier: str) -> Path:
        if self._lock is None:
            raise JournalError("journal must be open and exclusively locked")
        return self.directory / f"{kind}-{digest(identifier)}.json"

    def _read(self, path: Path) -> dict | None:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(value, dict) or value.get("version") != 1:
                raise ValueError("invalid record")
            return value
        except FileNotFoundError:
            return None
        except (OSError, ValueError, UnicodeError) as error:
            raise JournalError(f"cannot read journal record {path.name}; do not discard state") from error

    def _write(self, path: Path, value: dict) -> None:
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=self.directory, prefix=".pending-", delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(canonical(value))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            fd = os.open(self.directory, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        except OSError as error:
            raise JournalError("cannot persist journal; input must remain uncommitted") from error
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def find(self, payload: dict) -> dict | None:
        event_path = self._path("event", payload["eventId"].lower())
        event = {"version": 1, "requestId": payload["requestId"], "fingerprint": digest(payload)}
        previous = self._read(event_path)
        if previous is not None:
            if (set(previous) != set(event) or not isinstance(previous.get("requestId"), str)
                    or not self._fingerprint(previous.get("fingerprint"))):
                raise JournalError("invalid saved event; restore journal state")
            if previous != event:
                raise RequestConflict("eventId was reused with different content")
        record = self._read(self._path("request", payload["requestId"]))
        if record is not None:
            self._validate(record, payload)
        if previous is None:
            self._write(event_path, event)
        return record

    @staticmethod
    def _fingerprint(value):
        return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)

    @staticmethod
    def _validate(record: dict, payload: dict) -> None:
        result = record.get("result")
        if (not FileJournal._fingerprint(record.get("fingerprint"))
                or record.get("requestId") != payload["requestId"]
                or record.get("state") not in ("prepared", "published") or not isinstance(result, dict)
                or record.get("resultHash") != digest(result)
                or result.get("requestId") != payload["requestId"]
                or not isinstance(result.get("shiftId"), str)
                or result.get("status") not in ("OK", "TIMEOUT", "FAILED")
                or not isinstance(record.get("headers"), list)):
            raise JournalError("invalid saved result; do not recalculate after losing state")
        if (result["status"] in ("FAILED", "TIMEOUT")
                and (result.get("code") not in FAILURE_CODES or type(result.get("retryable")) is not bool
                     or not isinstance(result.get("error"), str))):
            raise JournalError("saved failure result does not match the current contract")
        if any(not isinstance(item, dict) or item.get("reason") not in UNSERVED_REASONS
               for item in result.get("unserved", [])):
            raise JournalError("saved unserved reasons do not match the current contract")
        try:
            for key, value in record["headers"]:
                if key != "traceparent":
                    raise ValueError("unexpected saved header")
                base64.b64decode(value, validate=True)
        except (ValueError, TypeError) as error:
            raise JournalError("invalid saved trace headers") from error

        if record["fingerprint"] != request_fingerprint(payload):
            raise RequestConflict("requestId was reused with different calculation input")
        if result["shiftId"] != payload["shiftId"]:
            raise JournalError("invalid saved result shiftId")

    def prepare(self, payload: dict, result: dict, headers: list[tuple[str, bytes]]) -> dict:
        record = {"version": 1, "requestId": payload["requestId"],
                  "fingerprint": request_fingerprint(payload), "state": "prepared", "result": result,
                  "resultHash": digest(result),
                  "headers": [[key, base64.b64encode(value).decode("ascii")] for key, value in headers]}
        self._validate(record, payload)
        self._write(self._path("request", payload["requestId"]), record)
        return record

    def mark_published(self, payload: dict, record: dict) -> None:
        self._write(self._path("request", payload["requestId"]), {**record, "state": "published"})

    @staticmethod
    def headers(record: dict) -> list[tuple[str, bytes]]:
        return [(key, base64.b64decode(value, validate=True)) for key, value in record["headers"]]
