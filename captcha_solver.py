"""
Captcha solver integration using ohmycaptcha.

This module provides a client to interact with the ohmycaptcha service
for solving various types of captchas including:
- reCAPTCHA v2/v3
- hCaptcha
- Cloudflare Turnstile
- Image-based captchas
"""

import asyncio
import base64
import os
from typing import Optional
from urllib.parse import urljoin

import httpx


class CaptchaSolverClient:
    """Client for the ohmycaptcha solving service."""

    def __init__(
        self,
        base_url: str = "http://localhost:8000",
        client_key: Optional[str] = None,
        timeout: float = 120.0,
    ):
        """
        Initialize the captcha solver client.

        Args:
            base_url: The base URL of the ohmycaptcha service
            client_key: Authentication key (defaults to env var CLIENT_KEY)
            timeout: Request timeout in seconds
        """
        self.base_url = base_url.rstrip("/")
        self.client_key = client_key or os.environ.get("CLIENT_KEY", "default-key")
        self.timeout = timeout
        self._client = httpx.AsyncClient(timeout=timeout)

    async def health_check(self) -> dict:
        """Check if the captcha service is healthy."""
        response = await self._client.get(urljoin(self.base_url, "/api/v1/health"))
        response.raise_for_status()
        return response.json()

    async def create_task(
        self,
        task_type: str,
        website_url: str,
        website_key: str,
        **kwargs,
    ) -> str:
        """
        Create a captcha solving task.

        Args:
            task_type: Type of captcha task (e.g., 'RecaptchaV3TaskProxyless')
            website_url: The URL of the website with the captcha
            website_key: The site key for the captcha
            **kwargs: Additional task-specific parameters

        Returns:
            The task ID for polling results
        """
        task_data = {
            "type": task_type,
            "websiteURL": website_url,
            "websiteKey": website_key,
        }
        task_data.update(kwargs)

        payload = {
            "clientKey": self.client_key,
            "task": task_data,
        }

        response = await self._client.post(
            urljoin(self.base_url, "/createTask"),
            json=payload,
        )
        response.raise_for_status()
        result = response.json()

        if result.get("errorId") != 0:
            raise CaptchaSolverError(
                f"Task creation failed: {result.get('errorDescription', 'Unknown error')}"
            )

        return result["taskId"]

    async def get_task_result(
        self,
        task_id: str,
        poll_interval: float = 2.0,
        max_attempts: int = 60,
    ) -> dict:
        """
        Poll for task result.

        Args:
            task_id: The task ID from create_task
            poll_interval: Seconds between polls
            max_attempts: Maximum number of polling attempts

        Returns:
            The task result containing the solution
        """
        payload = {
            "clientKey": self.client_key,
            "taskId": task_id,
        }

        for attempt in range(max_attempts):
            response = await self._client.post(
                urljoin(self.base_url, "/getTaskResult"),
                json=payload,
            )
            response.raise_for_status()
            result = response.json()

            if result.get("errorId") != 0:
                raise CaptchaSolverError(
                    f"Result polling failed: {result.get('errorDescription', 'Unknown error')}"
                )

            status = result.get("status")
            if status == "ready":
                return result.get("solution", {})
            elif status == "processing":
                await asyncio.sleep(poll_interval)
            else:
                raise CaptchaSolverError(f"Unknown task status: {status}")

        raise CaptchaSolverError(
            f"Task timed out after {max_attempts * poll_interval} seconds"
        )

    async def solve_recaptcha_v3(
        self,
        website_url: str,
        website_key: str,
        page_action: str = "verify",
        min_score: Optional[float] = None,
    ) -> str:
        """
        Solve reCAPTCHA v3.

        Returns:
            The gRecaptchaResponse token
        """
        kwargs = {"pageAction": page_action}
        if min_score is not None:
            kwargs["minScore"] = min_score

        task_id = await self.create_task(
            "RecaptchaV3TaskProxyless",
            website_url,
            website_key,
            **kwargs,
        )
        result = await self.get_task_result(task_id)
        return result.get("gRecaptchaResponse", "")

    async def solve_recaptcha_v2(
        self,
        website_url: str,
        website_key: str,
    ) -> str:
        """
        Solve reCAPTCHA v2.

        Returns:
            The gRecaptchaResponse token
        """
        task_id = await self.create_task(
            "RecaptchaV2TaskProxyless",
            website_url,
            website_key,
        )
        result = await self.get_task_result(task_id)
        return result.get("gRecaptchaResponse", "")

    async def solve_hcaptcha(
        self,
        website_url: str,
        website_key: str,
    ) -> str:
        """
        Solve hCaptcha.

        Returns:
            The gRecaptchaResponse token
        """
        task_id = await self.create_task(
            "HCaptchaTaskProxyless",
            website_url,
            website_key,
        )
        result = await self.get_task_result(task_id)
        return result.get("gRecaptchaResponse", "")

    async def solve_turnstile(
        self,
        website_url: str,
        website_key: str,
    ) -> str:
        """
        Solve Cloudflare Turnstile.

        Returns:
            The token
        """
        task_id = await self.create_task(
            "TurnstileTaskProxyless",
            website_url,
            website_key,
        )
        result = await self.get_task_result(task_id)
        return result.get("token", "")

    async def solve_image_captcha(
        self,
        image_path: str,
        question: Optional[str] = None,
    ) -> dict:
        """
        Solve image-based captcha.

        Args:
            image_path: Path to the captcha image
            question: Optional question about the image

        Returns:
            The solution (usually contains 'text' or 'objects')
        """
        # Read and encode image
        with open(image_path, "rb") as f:
            image_data = base64.b64encode(f.read()).decode("utf-8")

        kwargs = {"image": image_data}
        if question:
            kwargs["question"] = question

        task_id = await self.create_task(
            "ImageToTextTask",
            "",  # No website URL needed
            "",  # No website key needed
            **kwargs,
        )
        return await self.get_task_result(task_id)

    async def close(self):
        """Close the HTTP client."""
        await self._client.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()


class CaptchaSolverError(Exception):
    """Error raised by the captcha solver client."""

    pass


# Convenience functions for synchronous usage
def solve_recaptcha_v3_sync(
    website_url: str,
    website_key: str,
    page_action: str = "verify",
    base_url: str = "http://localhost:8000",
) -> str:
    """Synchronous wrapper for solving reCAPTCHA v3."""
    client = CaptchaSolverClient(base_url=base_url)
    return asyncio.run(client.solve_recaptcha_v3(website_url, website_key, page_action))


def solve_recaptcha_v2_sync(
    website_url: str,
    website_key: str,
    base_url: str = "http://localhost:8000",
) -> str:
    """Synchronous wrapper for solving reCAPTCHA v2."""
    client = CaptchaSolverClient(base_url=base_url)
    return asyncio.run(client.solve_recaptcha_v2(website_url, website_key))


def solve_turnstile_sync(
    website_url: str,
    website_key: str,
    base_url: str = "http://localhost:8000",
) -> str:
    """Synchronous wrapper for solving Cloudflare Turnstile."""
    client = CaptchaSolverClient(base_url=base_url)
    return asyncio.run(client.solve_turnstile(website_url, website_key))
