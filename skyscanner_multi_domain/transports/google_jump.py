"""Google search jump simulation — routes traffic through Google search to mimic organic
user behaviour, reducing bot detection signals.

Strategy:
1. Search Google for the Skyscanner route (e.g. "PEK to ALA flights skyscanner")
2. Click through the search result to land on Skyscanner
3. Return the landing page for parsing
"""

from __future__ import annotations

import asyncio
import random
import re
import time
from typing import Any, Optional
from urllib.parse import quote, urlencode, urlparse

from skyscanner_multi_domain.models import FlightQuote, RegionConfig
from skyscanner_multi_domain.transports.context import get_transport_context


# ── Search query builders ────────────────────────────────────────────────────


def _build_google_search_url(origin: str, destination: str, date: str) -> str:
    """Build a Google search URL for a Skyscanner route query."""
    query_text = f"{origin} to {destination} flights skyscanner {date}"
    params = urlencode({"q": query_text, "hl": "en", "num": "10"})
    return f"https://www.google.com/search?{params}"


def _build_skyscanner_search_snippets(origin: str, destination: str) -> list[str]:
    """Build variations of Google search queries that lead to Skyscanner."""
    return [
        f"{origin} to {destination} cheap flights skyscanner",
        f"skyscanner {origin} {destination} flights",
        f"{origin} {destination} flight tickets skyscanner",
        f"cheapest flights {origin} to {destination} skyscanner",
    ]


# ── Google search scraper ───────────────────────────────────────────────────


async def _fetch_google_search(
    query: str,
    *,
    timeout: float = 15.0,
) -> str:
    """Fetch Google search results page HTML."""
    import aiohttp
    ctx = get_transport_context()
    params = {"q": query, "hl": "en", "num": "10"}
    headers = {
        "User-Agent": ctx.random_ua(),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
    }
    url = f"https://www.google.com/search?{urlencode(params)}"
    timeout_obj = aiohttp.ClientTimeout(total=timeout)
    async with aiohttp.ClientSession(timeout=timeout_obj, headers=headers) as session:
        async with session.get(url) as resp:
            return await resp.text()


def _extract_skyscanner_links(html: str) -> list[str]:
    """Extract Skyscanner links from Google search results HTML."""
    # Match href attributes containing skyscanner domains
    pattern = re.compile(
        r'href="(https?://(?:www\.)?skyscanner\.[a-z.]+/[^"]*)"',
        re.IGNORECASE,
    )
    matches = pattern.findall(html)
    # Also try to extract from redirect URLs
    redirect_pattern = re.compile(
        r'/url\?q=(https?://(?:www\.)?skyscanner\.[a-z.]+/[^"&]+)',
        re.IGNORECASE,
    )
    redirect_matches = redirect_pattern.findall(html)
    all_links = matches + redirect_matches
    # Unescape and deduplicate
    seen: set[str] = set()
    result: list[str] = []
    for link in all_links:
        decoded = quote(link, safe=':/?=&%')
        if decoded not in seen:
            seen.add(decoded)
            result.append(decoded)
    return result


# ── CDP-based Google search jump (for browser transports) ───────────────────


async def _cdp_navigate_via_google(
    ws_url: str,
    skyscanner_url: str,
    origin: str,
    destination: str,
    *,
    timeout_ms: int = 30000,
) -> bool:
    """Navigate a CDP-controlled browser tab via Google search to Skyscanner.

    Steps:
    1. Navigate to google.com
    2. Type the search query
    3. Click a Skyscanner search result
    """
    from skyscanner_multi_domain.transports.scrapling import _cdp_send_command

    search_url = _build_google_search_url(origin, destination, "")
    try:
        # Navigate to Google search
        await _cdp_send_command(
            ws_url, "Page.navigate",
            {"url": search_url},
            timeout_seconds=30,
        )
        # Wait a realistic amount of time
        await asyncio.sleep(random.uniform(2.0, 4.0))

        # Extract Skyscanner links from the page
        result = await _cdp_send_command(
            ws_url, "Runtime.evaluate",
            {
                "expression": """
                (() => {
                    const links = document.querySelectorAll('a[href*="skyscanner"]');
                    return Array.from(links).slice(0, 5).map(a => a.href);
                })()
                """,
                "returnByValue": True,
            },
            timeout_seconds=10,
        )
        if isinstance(result, dict) and "result" in result:
            links = result["result"].get("value", [])
            if links and isinstance(links, list):
                skyscanner_links = [l for l in links if isinstance(l, str) and "skyscanner" in l.lower()]
                if skyscanner_links:
                    # Click through the first Skyscanner link
                    chosen = skyscanner_links[0]
                    await _cdp_send_command(
                        ws_url, "Page.navigate",
                        {"url": chosen},
                        timeout_seconds=15,
                    )
                    await asyncio.sleep(random.uniform(3.0, 6.0))
                    # Now navigate to the actual target URL
                    await _cdp_send_command(
                        ws_url, "Page.navigate",
                        {"url": skyscanner_url},
                        timeout_seconds=15,
                    )
                    return True
    except Exception:
        pass
    return False


# ── Main entry point ─────────────────────────────────────────────────────────


async def google_search_jump(
    skyscanner_url: str,
    origin: str,
    destination: str,
    date: str,
    *,
    use_cdp: bool = False,
    cdp_ws_url: str = "",
    timeout: float = 30.0,
) -> str | None:
    """Try to reach Skyscanner via Google search to reduce bot detection.

    Returns the Google referrer URL if successful, None otherwise.
    """
    if use_cdp and cdp_ws_url:
        success = await _cdp_navigate_via_google(
            cdp_ws_url, skyscanner_url, origin, destination,
        )
        if success:
            return _build_google_search_url(origin, destination, date)
        return None

    # HTTP-based approach: fetch Google results and extract Skyscanner links
    snippets = _build_skyscanner_search_snippets(origin, destination)
    query = random.choice(snippets)

    try:
        html = await _fetch_google_search(query, timeout=timeout)
        links = _extract_skyscanner_links(html)
        if links:
            # Return the Google search URL as a referrer
            return _build_google_search_url(origin, destination, date)
    except Exception:
        pass
    return None


async def build_quote_via_google_jump(
    region: RegionConfig,
    skyscanner_url: str,
    origin: str,
    destination: str,
    date: str,
    *,
    timeout: float = 30.0,
) -> FlightQuote | None:
    """Build a FlightQuote that includes a Google referrer, to reduce challenge rate."""
    import aiohttp
    ctx = get_transport_context()
    referrer = await google_search_jump(
        skyscanner_url, origin, destination, date, timeout=timeout,
    )
    headers = {
        "User-Agent": ctx.random_ua(),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    if referrer:
        headers["Referer"] = referrer

    try:
        timeout_obj = aiohttp.ClientTimeout(total=timeout)
        async with aiohttp.ClientSession(timeout=timeout_obj) as session:
            async with session.get(skyscanner_url, headers=headers) as resp:
                text = await resp.text()
                from skyscanner_multi_domain.parsing.page_parser import extract_page_quote
                quote = extract_page_quote(region, skyscanner_url, text)
                quote.source_kind = "google_referrer"
                return quote
    except Exception:
        pass
    return None
