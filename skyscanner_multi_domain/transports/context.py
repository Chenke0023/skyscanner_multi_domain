"""Transport context: UA rotation, proxy pool, shared resources for anti-detection.

Manages rotating User-Agent strings and proxy configurations across all transports.
"""

from __future__ import annotations

import random
import threading
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlparse

# ── User-Agent rotation pool ─────────────────────────────────────────────────

_DESKTOP_UA_POOL: list[str] = [
    # Chrome 131 on macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    # Chrome 131 on Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    # Chrome 130 on macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    # Edge 131 on Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0",
    # Edge 131 on macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0",
    # Chrome 129 on Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
    # Safari 18 on macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.1 Safari/605.1.15",
    # Firefox 133 on Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) Gecko/20100101 Firefox/133.0",
    # Firefox 133 on macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:133.0) Gecko/20100101 Firefox/133.0",
    # Chrome 131 on Linux
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
]

_MOBILE_UA_POOL: list[str] = [
    # Chrome on Android
    "Mozilla/5.0 (Linux; Android 14; Pixel 8 Pro) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.6778.135 Mobile Safari/537.36",
    # Safari on iPhone
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.1 Mobile/15E148 Safari/604.1",
    # Chrome on Android Samsung
    "Mozilla/5.0 (Linux; Android 14; SM-S928B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.6778.135 Mobile Safari/537.36",
]

# ── Proxy configuration ──────────────────────────────────────────────────────


@dataclass
class ProxyConfig:
    http: str = ""
    https: str = ""
    no_proxy_domains: list[str] = field(default_factory=list)

    @property
    def enabled(self) -> bool:
        return bool(self.http or self.https)

    def should_proxy(self, url: str) -> bool:
        if not self.enabled:
            return False
        domain = urlparse(url).netloc
        return domain not in self.no_proxy_domains

    def to_aiohttp_kwargs(self) -> dict[str, str]:
        kwargs: dict[str, str] = {}
        if self.http:
            kwargs["proxy"] = self.http
        return kwargs


# ── UA Rotator ───────────────────────────────────────────────────────────────


class UARotator:
    """Thread-safe User-Agent rotation with desktop/mobile pools."""

    def __init__(self, *, seed: int | None = None):
        self._desktop_pool = list(_DESKTOP_UA_POOL)
        self._mobile_pool = list(_MOBILE_UA_POOL)
        self._lock = threading.Lock()
        self._idx_desktop = 0
        self._idx_mobile = 0
        if seed is not None:
            random.seed(seed)
            random.shuffle(self._desktop_pool)
            random.shuffle(self._mobile_pool)

    def random_desktop(self) -> str:
        with self._lock:
            ua = self._desktop_pool[self._idx_desktop % len(self._desktop_pool)]
            self._idx_desktop += 1
            return ua

    def random_mobile(self) -> str:
        with self._lock:
            ua = self._mobile_pool[self._idx_mobile % len(self._mobile_pool)]
            self._idx_mobile += 1
            return ua

    def random(self) -> str:
        return self.random_desktop()


# ── TransportContext — shared resources ──────────────────────────────────────


class TransportContext:
    """Shared resources across transport layers: UA rotator, proxy config, thread pool."""

    def __init__(
        self,
        *,
        proxy: ProxyConfig | None = None,
        ua_rotator: UARotator | None = None,
        max_workers: int = 4,
    ):
        self.proxy = proxy or ProxyConfig()
        self.ua_rotator = ua_rotator or UARotator(seed=42)
        self._executor: Optional["ThreadPoolExecutor"] = None  # type: ignore[name-defined]
        self._max_workers = max_workers

    @property
    def executor(self) -> "ThreadPoolExecutor":  # type: ignore[name-defined]
        if self._executor is None:
            from concurrent.futures import ThreadPoolExecutor
            self._executor = ThreadPoolExecutor(max_workers=self._max_workers)
        return self._executor

    def random_ua(self) -> str:
        return self.ua_rotator.random()

    def get_proxy_kwargs(self, url: str) -> dict[str, str]:
        if self.proxy.should_proxy(url):
            return self.proxy.to_aiohttp_kwargs()
        return {}

    def shutdown(self):
        if self._executor is not None:
            self._executor.shutdown(wait=False)
            self._executor = None


# ── Global singleton ─────────────────────────────────────────────────────────

_global_context: TransportContext | None = None
_context_lock = threading.Lock()


def get_transport_context() -> TransportContext:
    global _global_context
    if _global_context is None:
        with _context_lock:
            if _global_context is None:
                _global_context = TransportContext()
    return _global_context


def configure_transport_context(
    *,
    proxy_http: str = "",
    proxy_https: str = "",
    no_proxy_domains: list[str] | None = None,
    max_workers: int = 4,
) -> TransportContext:
    global _global_context
    proxy = ProxyConfig(
        http=proxy_http,
        https=proxy_https,
        no_proxy_domains=no_proxy_domains or [],
    )
    with _context_lock:
        _global_context = TransportContext(proxy=proxy, max_workers=max_workers)
    return _global_context
