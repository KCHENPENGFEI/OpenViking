"""Shared HTTP client for novel-eval scripts."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)


class OvHttpError(RuntimeError):
    """Raised when the OpenViking server returns an unrecoverable error."""


_RETRY_DELAYS = (1.0, 3.0)  # 2 retries after the initial attempt


class OvClient:
    """Thin wrapper around httpx.Client that handles auth + retries + identity."""

    def __init__(
        self,
        *,
        base_url: str,
        user_api_key: str,
        account_id: Optional[str] = None,
        user_id: Optional[str] = None,
        timeout: float = 86400.0,
        transport: Optional[httpx.BaseTransport] = None,
    ):
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=timeout,
            transport=transport,
            headers={"X-API-Key": user_api_key},
        )
        # Identity params are an override; with a user API key the server
        # routes to the owning account/user automatically. Only attach when
        # the caller explicitly provides them.
        self._identity_params: dict[str, str] = {}
        if account_id:
            self._identity_params["account_id"] = account_id
        if user_id:
            self._identity_params["user_id"] = user_id

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "OvClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def post_json(self, path: str, *, json: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", path, json=json)

    def post_multipart(
        self,
        path: str,
        *,
        files: dict[str, Any],
        data: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        return self._request("POST", path, files=files, data=data)

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        last_text = ""
        last_status = 0
        for attempt, delay in enumerate([0.0, *_RETRY_DELAYS]):
            if delay:
                time.sleep(delay)
            try:
                resp = self._client.request(
                    method,
                    path,
                    params=self._identity_params or None,
                    **kwargs,
                )
            except httpx.RequestError as e:
                logger.warning("network error attempt=%d path=%s err=%s", attempt, path, e)
                last_text = str(e)
                continue

            if resp.status_code < 400:
                return resp.json()

            last_status = resp.status_code
            last_text = resp.text
            if resp.status_code < 500:
                break  # don't retry 4xx
            logger.warning(
                "server error attempt=%d path=%s status=%d body=%s",
                attempt,
                path,
                resp.status_code,
                resp.text[:200],
            )

        raise OvHttpError(f"{method} {path} failed status={last_status} body={last_text[:500]}")


class AsyncOvClient:
    """Async counterpart of OvClient — same auth/retry semantics."""

    def __init__(
        self,
        *,
        base_url: str,
        user_api_key: str,
        account_id: Optional[str] = None,
        user_id: Optional[str] = None,
        timeout: float = 300.0,
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ):
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=timeout,
            transport=transport,
            headers={"X-API-Key": user_api_key},
        )
        self._identity_params: dict[str, str] = {}
        if account_id:
            self._identity_params["account_id"] = account_id
        if user_id:
            self._identity_params["user_id"] = user_id

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "AsyncOvClient":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def post_json(self, path: str, *, json: dict[str, Any]) -> dict[str, Any]:
        return await self._request("POST", path, json=json)

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        last_text = ""
        last_status = 0
        for attempt, delay in enumerate([0.0, *_RETRY_DELAYS]):
            if delay:
                await asyncio.sleep(delay)
            try:
                resp = await self._client.request(
                    method,
                    path,
                    params=self._identity_params or None,
                    **kwargs,
                )
            except httpx.RequestError as e:
                logger.warning("network error attempt=%d path=%s err=%s", attempt, path, e)
                last_text = str(e)
                continue

            if resp.status_code < 400:
                return resp.json()

            last_status = resp.status_code
            last_text = resp.text
            if resp.status_code < 500:
                break
            logger.warning(
                "server error attempt=%d path=%s status=%d body=%s",
                attempt,
                path,
                resp.status_code,
                resp.text[:200],
            )

        raise OvHttpError(f"{method} {path} failed status={last_status} body={last_text[:500]}")
