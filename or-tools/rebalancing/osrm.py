"""OSRM Table adapter. The solver continues to consume only TravelMatrix."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from http.client import HTTPException
from math import ceil, isfinite
from time import monotonic
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

from .travel_time import LatLon, TravelMatrix, normalized_points


class TravelTimeError(RuntimeError):
    """A caller can decide retry policy; the adapter never retries or estimates."""

    def __init__(self, code: str, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.retryable = retryable


@dataclass(frozen=True)
class OsrmTravelTime:
    base_url: str
    profile: str = "driving"
    data_version: str = "unversioned"
    max_locations: int = 100
    timeout_s: float = 3.0
    budget_s: float = 8.0
    snap_radius_m: float = 1000.0

    def __post_init__(self) -> None:
        url = urlsplit(self.base_url)
        if (url.scheme not in ("http", "https") or not url.hostname or url.username
                or url.password or url.query or url.fragment):
            raise ValueError("OSRM URL must be http(s), without credentials, query or fragment")
        if not re.fullmatch(r"[A-Za-z0-9_-]+", self.profile):
            raise ValueError("invalid OSRM profile")
        if not isinstance(self.data_version, str) or not self.data_version.strip():
            raise ValueError("OSRM data_version must be non-empty")
        if type(self.max_locations) is not int or not 2 <= self.max_locations <= 1000:
            raise ValueError("OSRM max_locations must be 2..1000 and match server configuration")
        for name in ("timeout_s", "budget_s", "snap_radius_m"):
            value = getattr(self, name)
            if type(value) not in (int, float) or not isfinite(value) or value <= 0:
                raise ValueError(f"OSRM {name} must be finite and positive")

    @property
    def cache_namespace(self) -> str:
        # Version must identify PBF + routing profile + engine/algorithm settings.
        return json.dumps(["osrm-table-v1", self.base_url.rstrip("/"), self.profile,
                           self.data_version, self.snap_radius_m], separators=(",", ":"))

    def matrix(self, points: list[LatLon]) -> TravelMatrix:
        normalized = normalized_points(points)
        unique = list(dict.fromkeys(normalized))
        positions = {point: i for i, point in enumerate(unique)}
        size = len(unique)
        distances = [[0] * size for _ in unique]
        durations = [[0] * size for _ in unique]
        deadline = monotonic() + self.budget_s
        observed_version = None
        # Deduplicate depot/truck coordinates, then split both axes. Every
        # request contains at most max_locations unique coordinates.
        width = size if size <= self.max_locations else self.max_locations // 2
        for row in range(0, size, max(1, width)):
            sources = list(range(row, min(size, row + width)))
            for col in range(0, size, width):
                destinations = list(range(col, min(size, col + width)))
                indices = list(dict.fromkeys(sources + destinations))
                local = {index: i for i, index in enumerate(indices)}
                coords = ";".join(f"{unique[i][1]},{unique[i][0]}" for i in indices)
                query = urlencode({
                    "annotations": "distance,duration",
                    "sources": ";".join(str(local[i]) for i in sources),
                    "destinations": ";".join(str(local[i]) for i in destinations),
                    "radiuses": ";".join(str(self.snap_radius_m) for _ in indices),
                })
                remaining = deadline - monotonic()
                if remaining <= 0:
                    raise TravelTimeError("budget_exceeded", "OSRM matrix budget exceeded", retryable=True)
                payload = self._request(f"{self.base_url.rstrip('/')}/table/v1/{self.profile}/{coords}?{query}",
                                        min(self.timeout_s, remaining))
                if monotonic() > deadline:
                    raise TravelTimeError("budget_exceeded", "OSRM matrix budget exceeded", retryable=True)
                version = payload.get("data_version")
                if row == 0 and col == 0:
                    observed_version = version
                elif version != observed_version:
                    raise TravelTimeError("data_changed", "OSRM data_version changed between table blocks", retryable=True)
                for name, target in (("distances", distances), ("durations", durations)):
                    values = payload.get(name)
                    if (not isinstance(values, list) or len(values) != len(sources)
                            or any(not isinstance(r, list) or len(r) != len(destinations) for r in values)):
                        raise TravelTimeError("invalid_response", f"OSRM {name} dimensions are invalid")
                    for i, source in enumerate(sources):
                        for j, destination in enumerate(destinations):
                            value = values[i][j]
                            if value is None:
                                raise TravelTimeError("unreachable", "OSRM returned an unreachable pair")
                            if type(value) not in (int, float) or not isfinite(value) or not 0 <= value <= 1e9:
                                raise TravelTimeError("invalid_response", f"OSRM {name} value is invalid")
                            target[source][destination] = ceil(value)
        order = [positions[point] for point in normalized]
        result = TravelMatrix([[distances[i][j] for j in order] for i in order],
                              [[durations[i][j] for j in order] for i in order], source="osrm")
        try:
            result.validate(len(points))
        except ValueError as error:
            raise TravelTimeError("invalid_response", str(error)) from error
        return result

    def _request(self, url: str, timeout: float) -> dict:
        try:
            request = Request(url, headers={"Accept": "application/json", "User-Agent": "skala-optimizer/1"})
            with urlopen(request, timeout=timeout) as response:
                raw = response.read(8 * 1024 * 1024 + 1)
            if len(raw) > 8 * 1024 * 1024:
                raise TravelTimeError("invalid_response", "OSRM response exceeds 8 MiB")
            payload = json.loads(raw)
        except HTTPError as error:
            raise TravelTimeError("http_error", f"OSRM HTTP {error.code}",
                                  retryable=error.code == 429 or error.code >= 500) from error
        except (URLError, OSError, HTTPException) as error:
            raise TravelTimeError("network_error", f"OSRM connection failed: {type(error).__name__}", retryable=True) from error
        except (ValueError, UnicodeError) as error:
            raise TravelTimeError("invalid_response", "OSRM did not return valid JSON") from error
        if not isinstance(payload, dict):
            raise TravelTimeError("invalid_response", "OSRM response must be an object")
        if payload.get("code") != "Ok":
            code = payload.get("code", "missing_code")
            raise TravelTimeError("osrm_error", f"OSRM rejected table: {code}")
        if payload.get("fallback_speed_cells"):
            raise TravelTimeError("invalid_response", "OSRM estimated fallback cells are not supported")
        return payload
