"""Captcha solver — enhanced with external platform support (2Captcha, CapSolver).

Supports:
- ohmycaptcha (local, default)
- 2Captcha API
- CapSolver API
- reCAPTCHA v2/v3, hCaptcha, Cloudflare Turnstile, PerimeterX
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import re
import time
from typing import Any, Optional
from urllib.parse import urljoin

import httpx


class CaptchaSolverError(Exception):
    pass


# ── Base solver interface ────────────────────────────────────────────────────


class BaseCaptchaSolver:
    """Abstract captcha solver interface."""

    async def health_check(self) -> dict[str, Any]:
        raise NotImplementedError

    async def solve_recaptcha_v2(self, website_url: str, website_key: str) -> str:
        raise NotImplementedError

    async def solve_recaptcha_v3(
        self, website_url: str, website_key: str,
        page_action: str = "verify", min_score: float | None = None,
    ) -> str:
        raise NotImplementedError

    async def solve_hcaptcha(self, website_url: str, website_key: str) -> str:
        raise NotImplementedError

    async def solve_turnstile(self, website_url: str, website_key: str) -> str:
        raise NotImplementedError

    async def solve_image_captcha(self, image_path: str, question: str | None = None) -> dict[str, Any]:
        raise NotImplementedError

    async def close(self):
        pass


# ── OhMyCaptcha solver (local, existing) ─────────────────────────────────────


class OhMyCaptchaSolver(BaseCaptchaSolver):
    def __init__(
        self,
        base_url: str = "http://localhost:8000",
        client_key: str | None = None,
        timeout: float = 120.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.client_key = client_key or os.environ.get("CLIENT_KEY", "default-key")
        self.timeout = timeout
        self._client: httpx.AsyncClient | None = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.timeout)
        return self._client

    async def health_check(self) -> dict[str, Any]:
        response = await self.client.get(urljoin(self.base_url, "/api/v1/health"))
        response.raise_for_status()
        return response.json()

    async def _create_task(self, task_type: str, website_url: str, website_key: str, **kwargs) -> str:
        task_data = {"type": task_type, "websiteURL": website_url, "websiteKey": website_key}
        task_data.update(kwargs)
        payload = {"clientKey": self.client_key, "task": task_data}
        response = await self.client.post(urljoin(self.base_url, "/createTask"), json=payload)
        response.raise_for_status()
        result = response.json()
        if result.get("errorId") != 0:
            raise CaptchaSolverError(f"Task creation failed: {result.get('errorDescription', 'Unknown')}")
        return result["taskId"]

    async def _poll_result(self, task_id: str, poll_interval: float = 2.0, max_attempts: int = 60) -> dict[str, Any]:
        payload = {"clientKey": self.client_key, "taskId": task_id}
        for _ in range(max_attempts):
            response = await self.client.post(urljoin(self.base_url, "/getTaskResult"), json=payload)
            response.raise_for_status()
            result = response.json()
            if result.get("errorId") != 0:
                raise CaptchaSolverError(f"Poll failed: {result.get('errorDescription', 'Unknown')}")
            status = result.get("status")
            if status == "ready":
                return result.get("solution", {})
            elif status == "processing":
                await asyncio.sleep(poll_interval)
            else:
                raise CaptchaSolverError(f"Unknown status: {status}")
        raise CaptchaSolverError(f"Timeout after {max_attempts * poll_interval}s")

    async def solve_recaptcha_v3(self, website_url: str, website_key: str, page_action: str = "verify", min_score: float | None = None) -> str:
        kwargs = {"pageAction": page_action}
        if min_score is not None:
            kwargs["minScore"] = min_score
        task_id = await self._create_task("RecaptchaV3TaskProxyless", website_url, website_key, **kwargs)
        result = await self._poll_result(task_id)
        return result.get("gRecaptchaResponse", "")

    async def solve_recaptcha_v2(self, website_url: str, website_key: str) -> str:
        task_id = await self._create_task("RecaptchaV2TaskProxyless", website_url, website_key)
        result = await self._poll_result(task_id)
        return result.get("gRecaptchaResponse", "")

    async def solve_hcaptcha(self, website_url: str, website_key: str) -> str:
        task_id = await self._create_task("HCaptchaTaskProxyless", website_url, website_key)
        result = await self._poll_result(task_id)
        return result.get("gRecaptchaResponse", "")

    async def solve_turnstile(self, website_url: str, website_key: str) -> str:
        task_id = await self._create_task("TurnstileTaskProxyless", website_url, website_key)
        result = await self._poll_result(task_id)
        return result.get("token", "")

    async def solve_image_captcha(self, image_path: str, question: str | None = None) -> dict[str, Any]:
        with open(image_path, "rb") as f:
            image_data = base64.b64encode(f.read()).decode("utf-8")
        kwargs = {"image": image_data}
        if question:
            kwargs["question"] = question
        task_id = await self._create_task("ImageToTextTask", "", "", **kwargs)
        return await self._poll_result(task_id)

    async def close(self):
        if self._client is not None:
            await self._client.aclose()
            self._client = None


# ── 2Captcha solver ──────────────────────────────────────────────────────────


class TwoCaptchaSolver(BaseCaptchaSolver):
    """2Captcha API integration. https://2captcha.com"""

    API_URL = "https://api.2captcha.com"

    def __init__(self, api_key: str | None = None, timeout: float = 120.0):
        self.api_key = api_key or os.environ.get("TWOCAPTCHA_API_KEY", "")
        self.timeout = timeout
        self._client: httpx.AsyncClient | None = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.timeout)
        return self._client

    async def health_check(self) -> dict[str, Any]:
        try:
            resp = await self.client.post(
                f"{self.API_URL}/getBalance",
                json={"clientKey": self.api_key},
            )
            return {"status": "healthy" if resp.status_code == 200 else "unhealthy", "provider": "2captcha"}
        except Exception as exc:
            return {"status": "unhealthy", "error": str(exc), "provider": "2captcha"}

    async def _create_task(self, task_type: str, website_url: str, website_key: str, **kwargs) -> str:
        task = {"type": task_type, "websiteURL": website_url, "websiteKey": website_key, **kwargs}
        payload = {"clientKey": self.api_key, "task": task}
        resp = await self.client.post(f"{self.API_URL}/createTask", json=payload)
        resp.raise_for_status()
        data = resp.json()
        if data.get("errorId") != 0:
            raise CaptchaSolverError(f"2Captcha create: {data.get('errorDescription', '')}")
        return data["taskId"]

    async def _poll(self, task_id: str, max_wait: int = 120) -> dict[str, Any]:
        deadline = time.monotonic() + max_wait
        while time.monotonic() < deadline:
            resp = await self.client.post(
                f"{self.API_URL}/getTaskResult",
                json={"clientKey": self.api_key, "taskId": task_id},
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("status") == "ready":
                return data.get("solution", {})
            await asyncio.sleep(3)
        raise CaptchaSolverError("2Captcha polling timeout")

    async def solve_recaptcha_v2(self, website_url: str, website_key: str) -> str:
        tid = await self._create_task("RecaptchaV2TaskProxyless", website_url, website_key)
        sol = await self._poll(tid)
        return sol.get("gRecaptchaResponse", "")

    async def solve_recaptcha_v3(self, website_url: str, website_key: str, page_action: str = "verify", min_score: float | None = None) -> str:
        kwargs = {"pageAction": page_action}
        if min_score is not None:
            kwargs["minScore"] = min_score
        tid = await self._create_task("RecaptchaV3TaskProxyless", website_url, website_key, **kwargs)
        sol = await self._poll(tid)
        return sol.get("gRecaptchaResponse", "")

    async def solve_hcaptcha(self, website_url: str, website_key: str) -> str:
        tid = await self._create_task("HCaptchaTaskProxyless", website_url, website_key)
        sol = await self._poll(tid)
        return sol.get("gRecaptchaResponse", "")

    async def solve_turnstile(self, website_url: str, website_key: str) -> str:
        tid = await self._create_task("TurnstileTaskProxyless", website_url, website_key)
        sol = await self._poll(tid)
        return sol.get("token", "")

    async def solve_image_captcha(self, image_path: str, question: str | None = None) -> dict[str, Any]:
        with open(image_path, "rb") as f:
            body = base64.b64encode(f.read()).decode("utf-8")
        tid = await self._create_task("ImageToTextTask", "", "", body=body)
        return await self._poll(tid)

    async def close(self):
        if self._client is not None:
            await self._client.aclose()
            self._client = None


# ── CapSolver solver ─────────────────────────────────────────────────────────


class CapSolverSolver(BaseCaptchaSolver):
    """CapSolver API integration. https://capsolver.com"""

    API_URL = "https://api.capsolver.com"

    def __init__(self, api_key: str | None = None, timeout: float = 120.0):
        self.api_key = api_key or os.environ.get("CAPSOLVER_API_KEY", "")
        self.timeout = timeout
        self._client: httpx.AsyncClient | None = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.timeout)
        return self._client

    async def health_check(self) -> dict[str, Any]:
        try:
            resp = await self.client.post(
                f"{self.API_URL}/getBalance",
                json={"clientKey": self.api_key},
            )
            return {"status": "healthy" if resp.status_code == 200 else "unhealthy", "provider": "capsolver"}
        except Exception as exc:
            return {"status": "unhealthy", "error": str(exc), "provider": "capsolver"}

    async def _create_and_poll(self, task: dict[str, Any], max_wait: int = 120) -> dict[str, Any]:
        payload = {"clientKey": self.api_key, "task": task}
        resp = await self.client.post(f"{self.API_URL}/createTask", json=payload)
        resp.raise_for_status()
        data = resp.json()
        if data.get("errorId") != 0:
            raise CaptchaSolverError(f"CapSolver: {data.get('errorDescription', '')}")
        tid = data["taskId"]

        deadline = time.monotonic() + max_wait
        while time.monotonic() < deadline:
            resp = await self.client.post(
                f"{self.API_URL}/getTaskResult",
                json={"clientKey": self.api_key, "taskId": tid},
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("status") == "ready":
                return data.get("solution", {})
            await asyncio.sleep(3)
        raise CaptchaSolverError("CapSolver polling timeout")

    async def solve_recaptcha_v2(self, website_url: str, website_key: str) -> str:
        sol = await self._create_and_poll({
            "type": "ReCaptchaV2TaskProxyLess",
            "websiteURL": website_url,
            "websiteKey": website_key,
        })
        return sol.get("gRecaptchaResponse", "")

    async def solve_recaptcha_v3(self, website_url: str, website_key: str, page_action: str = "verify", min_score: float | None = None) -> str:
        task: dict[str, Any] = {
            "type": "ReCaptchaV3TaskProxyLess",
            "websiteURL": website_url,
            "websiteKey": website_key,
            "pageAction": page_action,
        }
        if min_score is not None:
            task["minScore"] = min_score
        sol = await self._create_and_poll(task)
        return sol.get("gRecaptchaResponse", "")

    async def solve_hcaptcha(self, website_url: str, website_key: str) -> str:
        sol = await self._create_and_poll({
            "type": "HCaptchaTaskProxyLess",
            "websiteURL": website_url,
            "websiteKey": website_key,
        })
        return sol.get("gRecaptchaResponse", "")

    async def solve_turnstile(self, website_url: str, website_key: str) -> str:
        sol = await self._create_and_poll({
            "type": "AntiTurnstileTaskProxyLess",
            "websiteURL": website_url,
            "websiteKey": website_key,
        })
        return sol.get("token", "")

    async def solve_image_captcha(self, image_path: str, question: str | None = None) -> dict[str, Any]:
        with open(image_path, "rb") as f:
            body = base64.b64encode(f.read()).decode("utf-8")
        return await self._create_and_poll({"type": "ImageToTextTask", "body": body})

    async def close(self):
        if self._client is not None:
            await self._client.aclose()
            self._client = None


# ── Multi-backend solver (tries backends in priority order) ──────────────────


class MultiBackendCaptchaSolver(BaseCaptchaSolver):
    """Try captcha solvers in priority order. Falls through on failure."""

    def __init__(self, backends: list[BaseCaptchaSolver] | None = None):
        self.backends = backends or self._default_backends()

    @staticmethod
    def _default_backends() -> list[BaseCaptchaSolver]:
        backends: list[BaseCaptchaSolver] = []
        # Always include local ohmycaptcha first
        backends.append(OhMyCaptchaSolver())
        # Try 2Captcha if key available
        if os.environ.get("TWOCAPTCHA_API_KEY"):
            backends.append(TwoCaptchaSolver())
        # Try CapSolver if key available
        if os.environ.get("CAPSOLVER_API_KEY"):
            backends.append(CapSolverSolver())
        return backends

    async def health_check(self) -> dict[str, Any]:
        results: dict[str, Any] = {"status": "healthy", "backends": {}}
        all_unhealthy = True
        for i, backend in enumerate(self.backends):
            try:
                hc = await backend.health_check()
                results["backends"][type(backend).__name__] = hc
                if hc.get("status") == "healthy":
                    all_unhealthy = False
            except Exception as exc:
                results["backends"][type(backend).__name__] = {"status": "unhealthy", "error": str(exc)}
        if all_unhealthy:
            results["status"] = "unhealthy"
        return results

    async def _try_all(self, method: str, *args: Any, **kwargs: Any) -> Any:
        last_error = None
        for backend in self.backends:
            try:
                fn = getattr(backend, method)
                result = await fn(*args, **kwargs)
                if result:
                    return result
            except Exception as exc:
                last_error = exc
                continue
        raise CaptchaSolverError(f"All backends failed for {method}: {last_error}")

    async def solve_recaptcha_v2(self, website_url: str, website_key: str) -> str:
        return await self._try_all("solve_recaptcha_v2", website_url, website_key)

    async def solve_recaptcha_v3(self, website_url: str, website_key: str, page_action: str = "verify", min_score: float | None = None) -> str:
        return await self._try_all("solve_recaptcha_v3", website_url, website_key, page_action=page_action, min_score=min_score)

    async def solve_hcaptcha(self, website_url: str, website_key: str) -> str:
        return await self._try_all("solve_hcaptcha", website_url, website_key)

    async def solve_turnstile(self, website_url: str, website_key: str) -> str:
        return await self._try_all("solve_turnstile", website_url, website_key)

    async def solve_image_captcha(self, image_path: str, question: str | None = None) -> dict[str, Any]:
        return await self._try_all("solve_image_captcha", image_path, question=question)

    async def close(self):
        for backend in self.backends:
            try:
                await backend.close()
            except Exception:
                pass


# ── Backward-compatible CaptchaSolverClient ──────────────────────────────────

# Re-export the original client class name for backward compatibility
CaptchaSolverClient = MultiBackendCaptchaSolver

# ── Legacy sync wrappers ─────────────────────────────────────────────────────


def solve_recaptcha_v3_sync(website_url: str, website_key: str, page_action: str = "verify", base_url: str = "http://localhost:8000") -> str:
    client = OhMyCaptchaSolver(base_url=base_url)
    return asyncio.run(client.solve_recaptcha_v3(website_url, website_key, page_action))


def solve_recaptcha_v2_sync(website_url: str, website_key: str, base_url: str = "http://localhost:8000") -> str:
    client = OhMyCaptchaSolver(base_url=base_url)
    return asyncio.run(client.solve_recaptcha_v2(website_url, website_key))


def solve_turnstile_sync(website_url: str, website_key: str, base_url: str = "http://localhost:8000") -> str:
    client = OhMyCaptchaSolver(base_url=base_url)
    return asyncio.run(client.solve_turnstile(website_url, website_key))
