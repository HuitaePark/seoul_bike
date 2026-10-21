"""Bounded memory and optional versioned disk cache; no database dependency."""
from __future__ import annotations

import hashlib
import json
import logging
import tempfile
from collections import OrderedDict
from dataclasses import asdict
from pathlib import Path
from time import time

from .travel_time import LatLon, TravelMatrix, TravelTime, normalized_points

log = logging.getLogger(__name__)


def _copy(matrix: TravelMatrix) -> TravelMatrix:
    return TravelMatrix([list(row) for row in matrix.distances_m],
                        [list(row) for row in matrix.durations_s], matrix.source)


class CachedTravelTime:
    """One instance per worker; full ordered matrices, not a shared-worker lock.

    Namespace must change when road data, Lua profile or routing settings change.
    Cache I/O failure does not discard a valid provider result. Provider failures
    are never cached. Atomic replace prevents partially written JSON being read.
    """

    def __init__(self, provider: TravelTime, *, namespace: str, cache_dir: str | Path | None = None,
                 max_entries: int = 16, ttl_s: float = 3600):
        if not isinstance(namespace, str) or not namespace.strip():
            raise ValueError("cache namespace must be non-empty")
        if type(max_entries) is not int or max_entries < 1:
            raise ValueError("cache max_entries must be positive")
        if type(ttl_s) not in (int, float) or not 0 < ttl_s < float("inf"):
            raise ValueError("cache ttl_s must be finite and positive")
        self.provider = provider
        self.namespace = namespace
        self.cache_dir = Path(cache_dir) if cache_dir is not None else None
        self.max_entries = max_entries
        self.ttl_s = ttl_s
        self._memory: OrderedDict[str, tuple[float, TravelMatrix]] = OrderedDict()
        self.hits = 0
        self.misses = 0

    def matrix(self, points: list[LatLon]) -> TravelMatrix:
        points = normalized_points(points)
        key = hashlib.sha256(json.dumps([1, self.namespace, points], separators=(",", ":"),
                                        allow_nan=False).encode()).hexdigest()
        now = time()
        entry = self._memory.pop(key, None)
        if entry is None and self.cache_dir is not None:
            try:
                payload = json.loads((self.cache_dir / f"{key}.json").read_text())
                if payload["key"] != key:
                    raise ValueError("cache key mismatch")
                matrix = TravelMatrix(**payload["matrix"])
                matrix.validate(len(points))
                expires = payload["expires"]
                if type(expires) not in (int, float) or not 0 < expires < float("inf"):
                    raise ValueError("invalid cache expiry")
                entry = (expires, matrix)
            except (OSError, ValueError, KeyError, TypeError):
                entry = None
        if entry is not None and entry[0] > now:
            self.hits += 1
            self._remember(key, *entry)
            return _copy(entry[1])
        self.misses += 1
        matrix = self.provider.matrix(points)
        matrix.validate(len(points))
        expires = time() + self.ttl_s
        self._remember(key, expires, _copy(matrix))
        if self.cache_dir is not None:
            self._write(key, expires, matrix)
        return _copy(matrix)

    def _remember(self, key: str, expires: float, matrix: TravelMatrix) -> None:
        self._memory[key] = (expires, matrix)
        while len(self._memory) > self.max_entries:
            self._memory.popitem(last=False)

    def _write(self, key: str, expires: float, matrix: TravelMatrix) -> None:
        temporary = None
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(mode="w", dir=self.cache_dir, prefix=".matrix-", delete=False) as stream:
                temporary = Path(stream.name)
                json.dump({"key": key, "expires": expires, "matrix": asdict(matrix)}, stream, allow_nan=False)
            temporary.replace(self.cache_dir / f"{key}.json")
            # The directory belongs exclusively to this cache. Bound disk growth
            # as truck positions change, also removing expired/obsolete entries.
            files = sorted(self.cache_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
            for old in files[self.max_entries:]:
                old.unlink(missing_ok=True)
        except OSError as error:
            log.warning("Travel matrix disk cache unavailable: %s", error)
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
