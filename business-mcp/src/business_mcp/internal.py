# Author: 정유은
# Date: 20OCT2026
# Purpose: 백엔드 /internal/** 호출 계약(Protocol)과 HTTP 구현. 오류는 RFC 9457 problem+json으로 받는다.

"""백엔드 /internal/** 계약. 원본은 docs/specs/internal-api.yaml.

business-mcp는 DB에 직접 접근하지 않고 업무 규칙도 갖지 않는다. 모든 값은 이 인터페이스로 받는다.
M1에서는 FakeInternalApi(fake_internal.py)를 쓰고, 백엔드가 준비되면 HttpInternalApi로 바꾼다.
요청·응답 필드는 계약 그대로 camelCase다.
"""

from __future__ import annotations

from typing import Any, Protocol

import httpx

from .errors import InternalApiError

Json = dict[str, Any]


class InternalApi(Protocol):
    async def risk_stations(self, region_id: str, horizon_h: int, limit: int) -> list[Json]: ...
    async def station_detail(self, station_id: str) -> Json: ...
    async def forecasts(self, station_ids: list[str], start: str, horizon_h: int) -> list[Json]: ...
    async def current_plan(self, shift_id: str) -> Json: ...
    async def truck_status(self, shift_id: str) -> list[Json]: ...
    async def field_reports(self, shift_id: str, since: str | None) -> list[Json]: ...
    async def audit_events(self, shift_id: str, since: str | None) -> list[Json]: ...
    async def solve(self, request: Json) -> Json: ...  # SolveRequest -> PlanCandidate

    # 변경 API: approvalId가 APPROVED가 아니면 백엔드가 403 APPROVAL_REQUIRED를 돌려준다.
    async def add_constraint(self, request: Json) -> Json: ...  # ConstraintRequest -> Constraint
    async def activate_plan(self, plan_id: str, approval: Json) -> Json: ...  # ApprovedChange -> Plan
    async def apply_route(self, truck_id: str, request: Json) -> Json: ...  # ApprovedChange + candidateId -> Route


class HttpInternalApi:
    """docker network 안의 백엔드(http://backend:8080)를 호출한다."""

    def __init__(
        self,
        base_url: str,
        *,
        token: str | None = None,
        timeout_s: float = 70.0,  # /internal/plans/solve는 백엔드가 최대 60초 기다린다
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        headers = {"X-Internal-Token": token} if token else None
        self._client = httpx.AsyncClient(base_url=base_url, timeout=timeout_s, headers=headers, transport=transport)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            res = await self._client.request(method, path, **kwargs)
        except httpx.TimeoutException:
            raise InternalApiError(
                "UPSTREAM_UNAVAILABLE", "백엔드 응답이 늦습니다. 잠시 뒤 다시 호출하세요.",
                retryable=True, hint={"retry_after_s": 5},
            )
        except httpx.TransportError:
            raise InternalApiError(
                "UPSTREAM_UNAVAILABLE", "백엔드에 연결할 수 없습니다. 잠시 뒤 다시 호출하세요.",
                retryable=True, hint={"retry_after_s": 5},
            )
        if res.status_code >= 500:
            retry_after = res.headers.get("Retry-After", "")
            raise InternalApiError(
                "UPSTREAM_UNAVAILABLE", f"백엔드 오류({res.status_code})입니다. 잠시 뒤 다시 호출하세요.",
                retryable=True, hint={"retry_after_s": int(retry_after) if retry_after.isdigit() else 5},
            )
        if res.status_code >= 400:
            try:
                body = res.json()
            except ValueError:
                body = {}
            raise InternalApiError.from_problem(res.status_code, body if isinstance(body, dict) else {})
        return res.json()

    async def risk_stations(self, region_id, horizon_h, limit):
        return await self._request(
            "GET", "/internal/stations/risk", params={"regionId": region_id, "horizonH": horizon_h, "limit": limit},
        )

    async def station_detail(self, station_id):
        return await self._request("GET", f"/internal/stations/{station_id}")

    async def forecasts(self, station_ids, start, horizon_h):
        return await self._request(
            "GET", "/internal/forecasts",
            params={"stationIds": ",".join(station_ids), "start": start, "horizonH": horizon_h},
        )

    async def current_plan(self, shift_id):
        return await self._request("GET", f"/internal/shifts/{shift_id}/plan")

    async def truck_status(self, shift_id):
        return await self._request("GET", f"/internal/shifts/{shift_id}/trucks")

    async def field_reports(self, shift_id, since):
        return await self._request(
            "GET", f"/internal/shifts/{shift_id}/reports", params={"since": since} if since else None,
        )

    async def audit_events(self, shift_id, since):
        return await self._request(
            "GET", f"/internal/shifts/{shift_id}/audit", params={"since": since} if since else None,
        )

    async def solve(self, request):
        return await self._request("POST", "/internal/plans/solve", json=request)

    async def add_constraint(self, request):
        return await self._request("POST", "/internal/constraints", json=request)

    async def activate_plan(self, plan_id, approval):
        return await self._request("POST", f"/internal/plans/{plan_id}/activate", json=approval)

    async def apply_route(self, truck_id, request):
        return await self._request("POST", f"/internal/routes/{truck_id}/apply", json=request)


# backend가 /internal/**를 단계적으로 연다 (PR #19·#20: 현장 보고, 제약 추가, 감사 기록). 열린 것만 http로 부른다.
HTTP_READY_METHODS = ("field_reports", "add_constraint", "audit_events")


class RoutedInternalApi:
    """메서드마다 다른 구현을 쓴다. routes에 있는 메서드는 그 구현으로, 나머지는 default로 부른다 (INTERNAL_MODE=hybrid)."""

    def __init__(self, default: InternalApi, routes: dict[str, InternalApi]) -> None:
        self._default = default
        self._routes = routes

    def __getattr__(self, name: str) -> Any:
        return getattr(self._routes.get(name, self._default), name)
