from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Optional

from skyscanner_multi_domain.models import FlightQuote, RegionConfig
from skyscanner_multi_domain.parsing.price_candidates import (
    PriceCandidate,
    collect_price_candidates,
    confidence_to_float,
    rank_price_candidates,
    selected_candidate_to_metadata,
)


PAGE_TEXT_CAPTURE_LIMIT = 80000
PAGE_TEXT_CAPTURE_CONTEXT = 4000

CHALLENGE_HINTS = (
    "verify you are human",
    "verify you're human",
    "security check",
    "complete the challenge",
    "captcha",
    "press and hold",
    "are you a robot",
    "人机验证",
    "验证你是人类",
    "安全检查",
)
LOADING_HINTS = (
    "searching for the best flights",
    "searching flights",
    "looking for the best flights",
    "正在搜索",
    "搜索中",
    "查找最优惠",
    "loading",
)
REDIRECT_HINTS = (
    "go to skyscanner",
    "take me to",
    "we've found a better",
    "redirecting",
    "前往",
    "带我去",
    "找到更好的",
    "正在重定向",
    "switch to",
)
UNSUPPORTED_ROUTE_HINTS = (
    "we don't fly",
    "no routes",
    "route not supported",
    "try another route",
    "不提供",
    "没有航线",
    "不支持此航线",
    "尝试其他航线",
    "sorry, we don't",
)
NO_FLIGHTS_HINTS = (
    "no flights found",
    "no results",
    "try different dates",
    "try another date",
    "unavailable",
    "no flight results",
    "没有找到航班",
    "无结果",
    "未找到航班",
    "0 results",
)
CURRENCY_TOKENS = (
    "HK$",
    "US$",
    "CA$",
    "A$",
    "S$",
    "€",
    "£",
    "¥",
    "$",
    "₩",
    "₹",
    "₽",
    "₺",
    "₫",
    "CHF",
    "SEK",
    "NOK",
    "DKK",
    "ISK",
    "PLN",
    "CZK",
    "HUF",
    "RON",
    "BGN",
    "HRK",
    "RSD",
    "AED",
    "QAR",
    "SAR",
    "KWD",
    "BHD",
    "OMR",
    "JPY",
    "CNY",
    "HKD",
    "USD",
    "GBP",
    "SGD",
    "KRW",
    "IDR",
    "EUR",
    "AUD",
    "CAD",
    "INR",
    "BRL",
    "MXN",
    "RUB",
    "KZT",
)
BEST_LABELS = (
    "Best",
    "Best option",
    "Best flight",
    "Recommended",
    "最佳",
    "最佳选项",
    "最优",
    "推荐",
    "推荐排序",
    "Bäst",
    "추천순",
    "おすすめ",
    "おすすめ順",
    "おすすめのフライト",
    "Am besten",
    "Mejor",
    "Migliore",
    "Melhor",
    "Beste",
    "Le meilleur",
    "Najlepsze",
    "Cel mai bun",
    "Лучший",
    "Лучшее",
    "Terbaik",
)
CHEAPEST_LABELS = (
    "Cheapest",
    "最便宜",
    "Billigast",
    "최저가",
    "最安値",
    "Günstigste",
    "Más barato",
    "Più economico",
    "Mais barato",
    "Goedkoopste",
    "Le moins cher",
    "Najtańsze",
    "Cel mai ieftin",
    "Самый дешевый",
    "Самые дешевые",
    "Дешевле всего",
    "Termurah",
)
SORT_LABELS = tuple(dict.fromkeys((*BEST_LABELS, *CHEAPEST_LABELS)))
LABEL_PRICE_SCAN_LINES = 10
PRICE_PREFIX_HINTS = (
    "总费用为",
    "費用總計",
    "总费用",
    "價格低至",
    "价格低至",
    "最低只要",
    "起",
)
SORT_SECTION_HINTS = (
    "Visa resultat efter",
    "搜索结果显示方式",
    "搜尋結果顯示方式",
    "검색 결과 표시 기준",
    "検索結果の表示順",
    "Show results by",
    "Display results by",
    "Mostrar resultados por",
    "Mostra risultati per",
    "Afficher les résultats par",
    "Ergebnisse anzeigen nach",
    "Показать результаты по",
    "Tampilkan hasil berdasarkan",
)
REGION_BEST_LABELS: dict[str, tuple[str, ...]] = {
    "SE": ("Bäst",),
    "KR": ("추천순",),
    "JP": ("おすすめ", "おすすめ順"),
    "CN": ("综合最佳", "最优", "最佳"),
    "HK": ("最優", "最佳"),
    "SG": ("综合最佳", "最优", "最佳"),
    "DE": ("Am besten", "Beste"),
    "FR": ("Le meilleur",),
    "ES": ("Mejor",),
    "IT": ("Migliore",),
    "PT": ("Melhor",),
    "NL": ("Beste",),
    "PL": ("Najlepsze",),
    "RO": ("Cel mai bun",),
    "ID": ("Terbaik",),
    "KZ": ("Лучший", "Лучшее"),
    "RU": ("Лучший", "Лучшее"),
}
REGION_CHEAPEST_LABELS: dict[str, tuple[str, ...]] = {
    "SE": ("Billigast",),
    "KR": ("최저가",),
    "JP": ("最安値",),
    "CN": ("最便宜",),
    "HK": ("最便宜",),
    "SG": ("最便宜",),
    "DE": ("Günstigste",),
    "FR": ("Le moins cher",),
    "ES": ("Más barato",),
    "IT": ("Più economico",),
    "PT": ("Mais barato",),
    "NL": ("Goedkoopste",),
    "PL": ("Najtańsze",),
    "RO": ("Cel mai ieftin",),
    "ID": ("Termurah",),
    "KZ": ("Самый дешевый", "Самые дешевые", "Дешевле всего"),
    "RU": ("Самый дешевый", "Самые дешевые", "Дешевле всего"),
}
PARSER_FALLBACK_STATUSES = {
    "page_text_best_only",
    "page_text_cheapest_only",
    "page_text_fallback",
    "page_text_inconsistent",
    "page_text_recovered_best",
}
PARSER_REPLAYABLE_FAILURE_STATUSES = {
    "page_challenge",
    "page_loading",
    "page_parse_failed",
}


@dataclass(frozen=True)
class ParsedPrice:
    currency: str
    price: float
    raw_text: str
    match_kind: str


@dataclass(frozen=True)
class LabeledPriceCandidate:
    label: str
    currency: str
    price: float
    score: int
    hint_score: int
    distance: int
    line_index: int
    raw_block: str


@dataclass(frozen=True)
class LabeledPriceSearch:
    candidates: tuple[LabeledPriceCandidate, ...]
    matched_labels: int
    unparsed_blocks: tuple[str, ...]


@dataclass(frozen=True)
class PageStateRecognition:
    state: str
    challenge_hint: Optional[str]
    loading_hint: Optional[str]
    redirect_hint: Optional[str] = None
    unsupported_hint: Optional[str] = None
    no_flights_hint: Optional[str] = None
    is_blank: bool = False
    scope_strategy: str = "unknown"
    sort_marker_found: bool = False
    scoped_price_found: bool = False


@dataclass(frozen=True)
class PageParseDiagnostics:
    state: PageStateRecognition
    best_candidates: tuple[LabeledPriceCandidate, ...]
    cheapest_candidates: tuple[LabeledPriceCandidate, ...]
    fallback_price: Optional[ParsedPrice]
    selected_best: Optional[LabeledPriceCandidate]
    selected_cheapest: Optional[LabeledPriceCandidate]
    final_status: str
    validation_outcome: str
    failure_stage: Optional[str]
    failure_reason: Optional[str]
    used_fallback: bool


# ── Route/date extraction ──────────────────────────────────────────────────────

_ROUTE_CODE_PATTERN = re.compile(
    r"\b([A-Z]{2,3})\s*(?:→|->|to|→|ー>?)\s*([A-Z]{2,3})\b",
    re.IGNORECASE,
)
_DATE_PATTERNS = (
    re.compile(r"\b(\d{1,2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a]*\.?\s+(\d{4})\b", re.IGNORECASE),
    re.compile(r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a]*\.?\s+(\d{1,2}),?\s+(\d{4})\b", re.IGNORECASE),
    re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b"),
    re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b"),
    re.compile(r"\b(\d{1,2})\s+(?:月|日)\s+(\d{4})\b"),
)
_ROUTE_CODE_IN_TEXT = re.compile(r"\b([A-Z]{2,3})\b")


def _normalize_route_code(code: str) -> str:
    return code.strip().upper()


def _extract_route_from_text(text: str) -> tuple[Optional[str], Optional[str]]:
    """Extract origin→destination from page text. Returns (origin, destination) or (None, None)."""
    text_lower = text.lower()
    # Look for explicit route patterns
    for match in _ROUTE_CODE_PATTERN.finditer(text):
        o, d = _normalize_route_code(match.group(1)), _normalize_route_code(match.group(2))
        # Validate they look like airport codes (2-3 letters)
        if len(o) >= 2 and len(d) >= 2 and o != d:
            return o, d
    return None, None


def _extract_date_from_text(text: str, expected_year: str = "") -> Optional[str]:
    """Extract the most prominent date from page text. Returns ISO date string or None."""
    # Collect all date matches with their position
    candidates: list[tuple[int, str]] = []
    for pattern in _DATE_PATTERNS:
        for m in pattern.finditer(text):
            parts = m.groups()
            try:
                if len(parts) == 3:
                    if pattern.pattern.startswith(r"\b(\d{4})"):
                        # YYYY-MM-DD
                        y, mo, d = parts
                        date_str = f"{y}-{mo:0>2}-{d:0>2}"
                    elif "Jan" in pattern.pattern or "Feb" in pattern.pattern or "Mar" in pattern.pattern:
                        # DD Mon YYYY or Mon DD, YYYY
                        # parts[0] is digit → DD Mon YYYY (e.g. "20 May 2026")
                        # parts[0] is alpha → Mon DD YYYY (e.g. "May 20 2026")
                        if parts[0].isdigit():
                            d, mon, y = parts
                        else:
                            mon, d, y = parts
                        mo_num = {"jan":1,"feb":2,"mar":3,"apr":4,"may":5,"jun":6,
                                  "jul":7,"aug":8,"sep":9,"oct":10,"nov":11,"dec":12}.get(mon[:3].lower(), 1)
                        date_str = f"{y}-{mo_num:0>2}-{d:0>2}"
                    elif "月" in pattern.pattern or "日" in pattern.pattern:
                        d, y = parts
                        date_str = f"{y}-01-{d:0>2}"
                    else:
                        continue
                    candidates.append((m.start(), date_str))
            except (ValueError, KeyError):
                continue
    if not candidates:
        return None
    # Return the date closest to the start of the text (most likely the main trip date)
    candidates.sort(key=lambda x: x[0])
    return candidates[0][1]


# ── Sanity check ───────────────────────────────────────────────────────────────

SEMANTIC_MISMATCH_STATUS = "page_semantic_mismatch"


def sanity_check_quote(
    quote: FlightQuote,
    region: "RegionConfig",
    source_url: str,
    page_text: str,
    expected_origin: str,
    expected_destination: str,
    expected_date: str,
) -> FlightQuote:
    """Validate that a successfully-parsed quote actually matches the requested route/date/currency.

    Sets route_mismatch / date_mismatch / currency_mismatch flags on the quote.
    If any mismatch is found, downgrades confidence to <= 0.3 and sets status to
    page_semantic_mismatch so the result is not silently accepted as valid.
    """
    # Extract what we can from page text and URL
    route_origin, route_dest = _extract_route_from_text(page_text)
    detected_date = _extract_date_from_text(page_text, expected_year=expected_date[:4] if expected_date else "")

    # Extract currency from page text
    import re as _re
    currency_match = _re.search(
        r"\b(AUD|GBP|CAD|CNY|EUR|HKD|INR|JPY|KRW|KZT|MYR|PHP|SGD|THB|USD|VND|RUB|CHF|SEK|NOK|DKK|PLN|CZK|HUF|RON|BGN|HRK|AED|QAR|SAR|KWD|BHD|OMR|RSD|TRY|ZAR|MXN|BRL|CLP|COP|PEN|PYG|UYU)\b",
        page_text,
    )
    currency_detected = currency_match.group(1) if currency_match else None

    # Normalize
    expected_origin_n = expected_origin.strip().upper()
    expected_dest_n = expected_destination.strip().upper()
    route_origin_n = route_origin.strip().upper() if route_origin else None
    route_dest_n = route_dest.strip().upper() if route_dest else None

    # Check mismatches
    route_ok = (
        route_origin_n == expected_origin_n and route_dest_n == expected_dest_n
    ) if route_origin_n and route_dest_n else None  # None = couldn't detect

    date_ok: Optional[bool] = None
    if detected_date and expected_date:
        date_ok = _normalize_date(detected_date) == _normalize_date(expected_date)

    currency_ok: Optional[bool] = None
    if currency_detected and region.currency:
        currency_ok = currency_detected.upper() == region.currency.upper()

    quote.route_detected = f"{route_origin}→{route_dest}" if route_origin and route_dest else None
    quote.date_detected = detected_date
    quote.currency_detected = currency_detected

    mismatches: list[str] = []
    if route_ok is False:
        quote.route_mismatch = True
        mismatches.append(
            f"route mismatch: expected {expected_origin_n}→{expected_dest_n}, "
            f"detected {quote.route_detected}"
        )
    if date_ok is False:
        quote.date_mismatch = True
        mismatches.append(
            f"date mismatch: expected {expected_date}, detected {detected_date}"
        )
    if currency_ok is False:
        quote.currency_mismatch = True
        mismatches.append(
            f"currency mismatch: expected {region.currency}, detected {currency_detected}"
        )

    if mismatches:
        quote.status = SEMANTIC_MISMATCH_STATUS
        quote.error = "; ".join(mismatches)
        quote.confidence = 0.3
        if not quote.parser_warnings:
            quote.parser_warnings = []
        mismatch_warning = f"语义校验失败: {quote.error}"
        if mismatch_warning not in quote.parser_warnings:
            quote.parser_warnings.append(mismatch_warning)

    return quote


def _normalize_date(date_str: str) -> str:
    """Normalize a date string to YYYY-MM-DD."""
    from datetime import datetime
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%Y/%m/%d"):
        for try_fmt in (fmt, fmt.replace("/", "-")):
            try:
                return datetime.strptime(date_str.strip(), try_fmt).strftime("%Y-%m-%d")
            except ValueError:
                pass
    return date_str.strip()
def attach_parser_trust_metadata(
    quote: FlightQuote,
    diagnostics: PageParseDiagnostics,
) -> FlightQuote:
    warnings: list[str] = []
    price_source = "unpriced"
    evidence_text = diagnostics.failure_reason or ""
    confidence = 0.0

    if diagnostics.selected_cheapest is not None:
        price_source = "cheapest_block"
        confidence = 0.9
        evidence_text = diagnostics.selected_cheapest.raw_block
    elif diagnostics.selected_best is not None:
        price_source = "best_block"
        confidence = 0.78
        evidence_text = diagnostics.selected_best.raw_block
        warnings.append("仅解析到 Best 区块，未解析到 Cheapest 对照。")
    elif diagnostics.fallback_price is not None and quote.price is not None:
        price_source = "first_price_fallback"
        confidence = 0.45
        evidence_text = diagnostics.fallback_price.raw_text
        warnings.append("使用页面首个价格 fallback，需人工点开确认。")

    if diagnostics.validation_outcome == "recovered_best":
        price_source = "recovered_best"
        confidence = min(confidence, 0.72)
        warnings.append("Best 初始候选与 Cheapest 不一致，已使用恢复后的 Best 候选。")
    elif diagnostics.validation_outcome == "ignored_inconsistent_best":
        confidence = min(confidence or 0.55, 0.55)
        warnings.append("Best/Cheapest 不一致，已忽略错位 Best 候选。")
    elif diagnostics.validation_outcome in {"best_only", "cheapest_only"}:
        confidence = min(confidence or 0.7, 0.7)
        warnings.append("Best/Cheapest 只解析到一侧，可信度降低。")

    if (
        diagnostics.selected_best is not None
        and diagnostics.selected_cheapest is not None
        and diagnostics.selected_best.price != diagnostics.selected_cheapest.price
    ):
        warnings.append("Best 与 Cheapest 价格不一致，请点开页面确认票价条件。")

    quote.confidence = round(confidence, 2)
    quote.price_source = price_source
    quote.evidence_text = " ".join(evidence_text.split())[:240] if evidence_text else None
    quote.parser_warnings = warnings
    return quote


def attach_price_candidate_metadata(
    quote: FlightQuote,
    candidates: list[PriceCandidate],
) -> FlightQuote:
    selected, sources, rank = selected_candidate_to_metadata(candidates)
    quote.price_candidates_count = len(candidates)
    quote.selected_candidate_rank = rank
    quote.candidate_sources = sources
    if selected is None:
        return quote
    if not quote.evidence_text:
        quote.evidence_text = " ".join(selected.evidence_text.split())[:240]
    for warning in selected.warning_flags:
        text = {
            "currency_mismatch": "候选价格币种与市场默认币种不一致。",
            "suspicious_low_price": "候选价格异常偏低，需人工确认。",
            "suspicious_high_price": "候选价格异常偏高，需人工确认。",
            "non_itinerary_context": "候选价格可能来自日历/广告/非机票上下文。",
        }.get(warning, warning)
        if text not in quote.parser_warnings:
            quote.parser_warnings.append(text)
    return quote


def _labeled_candidate_to_price_candidate(
    candidate: LabeledPriceCandidate,
    source: str,
) -> PriceCandidate:
    return PriceCandidate(
        amount=candidate.price,
        currency=candidate.currency,
        source=source,
        confidence="unknown",
        evidence_text=candidate.raw_block,
        marker=candidate.label,
        marker_distance=candidate.distance,
    )


def _build_candidate_list(
    page_text: str,
    region: RegionConfig,
    best_search: LabeledPriceSearch,
    cheapest_search: LabeledPriceSearch,
    fallback_price: ParsedPrice | None,
) -> list[PriceCandidate]:
    candidates: list[PriceCandidate] = []
    candidates.extend(
        _labeled_candidate_to_price_candidate(candidate, "best_block")
        for candidate in best_search.candidates
    )
    candidates.extend(
        _labeled_candidate_to_price_candidate(candidate, "cheapest_block")
        for candidate in cheapest_search.candidates
    )
    # Only include fallback price in candidate ranking when no labeled candidates exist.
    # Low-confidence fallback should not dilute ranking when Best/Cheapest blocks are present.
    if fallback_price is not None and not best_search.candidates and not cheapest_search.candidates:
        candidates.append(
            PriceCandidate(
                amount=fallback_price.price,
                currency=fallback_price.currency,
                source="first_price_fallback",
                confidence="low",
                evidence_text=fallback_price.raw_text,
            )
        )
    candidates.extend(collect_price_candidates(page_text, region.currency))
    return rank_price_candidates(candidates, region.currency)


def parse_float(value: Any) -> Optional[float]:
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    cleaned = re.sub(r"[^\d,\.\s\u00a0]", "", text)
    cleaned = cleaned.replace("\u00a0", " ").strip()
    if not cleaned:
        return None
    if "," in cleaned and "." in cleaned:
        if cleaned.rfind(",") > cleaned.rfind("."):
            cleaned = cleaned.replace(".", "")
            cleaned = cleaned.replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")
    elif "." in cleaned:
        parts = [part for part in cleaned.split(".") if part]
        if len(parts) > 1 and len(parts[-1]) in {1, 2}:
            cleaned = "".join(parts[:-1]) + "." + parts[-1]
        elif len(parts) > 1 and all(part.isdigit() for part in parts):
            cleaned = "".join(parts)
    elif "," in cleaned:
        parts = [part for part in cleaned.split(",") if part]
        if len(parts) > 1 and len(parts[-1]) in {1, 2}:
            cleaned = "".join(parts[:-1]) + "." + parts[-1]
        else:
            cleaned = "".join(parts)
    cleaned = cleaned.replace(" ", "")
    if cleaned.isdigit():
        match_text = cleaned
    else:
        match = re.search(r"\d+(?:\.\d+)?", cleaned)
        if not match:
            return None
        match_text = match.group(0)
    try:
        return float(match_text)
    except ValueError:
        return None


def first_currency(value: Any) -> Optional[str]:
    if isinstance(value, str):
        cleaned = value.strip()
        if 1 <= len(cleaned) <= 6:
            return cleaned.upper()
    return None


def parse_price_fragment(text: str) -> Optional[ParsedPrice]:
    token_pattern = "|".join(
        re.escape(token) for token in sorted(CURRENCY_TOKENS, key=len, reverse=True)
    )
    amount_pattern = r"\d[\d\s\u00a0,.]*"

    prefix_match = re.search(
        rf"({token_pattern})[ \t\u00a0]*({amount_pattern})",
        text,
        re.IGNORECASE,
    )
    if prefix_match:
        amount = parse_float(prefix_match.group(2))
        if amount is not None:
            return ParsedPrice(
                currency=prefix_match.group(1),
                price=amount,
                raw_text=prefix_match.group(0),
                match_kind="prefix",
            )

    suffix_match = re.search(
        rf"({amount_pattern})[ \t\u00a0]*({token_pattern})",
        text,
        re.IGNORECASE,
    )
    if suffix_match:
        amount = parse_float(suffix_match.group(1))
        if amount is not None:
            return ParsedPrice(
                currency=suffix_match.group(2),
                price=amount,
                raw_text=suffix_match.group(0),
                match_kind="suffix",
            )

    return None


def parse_price_text(text: str) -> Optional[tuple[str, float]]:
    parsed = parse_price_fragment(text)
    if parsed is None:
        return None
    return parsed.currency, parsed.price


def find_page_hint(page_text: str, hints: tuple[str, ...]) -> Optional[str]:
    lower_text = page_text.lower()
    for hint in hints:
        if hint in lower_text:
            return hint
    return None


def find_first_sort_marker(page_text: str) -> int:
    lower_text = page_text.lower()
    indexes = [
        index
        for marker in (*SORT_SECTION_HINTS, *SORT_LABELS)
        for index in [lower_text.find(marker.lower())]
        if index >= 0
    ]
    return min(indexes) if indexes else -1


def slice_page_text_for_scan(
    page_text: str,
    *,
    max_chars: int = PAGE_TEXT_CAPTURE_LIMIT,
    context_chars: int = PAGE_TEXT_CAPTURE_CONTEXT,
) -> str:
    if len(page_text) <= max_chars:
        return page_text

    marker_index = find_first_sort_marker(page_text)
    if marker_index < 0:
        return page_text[:max_chars]

    start = max(marker_index - context_chars, 0)
    return page_text[start : start + max_chars]


def get_flight_results_scope_details(page_text: str) -> tuple[str, str]:
    page_text = slice_page_text_for_scan(page_text)
    lower_text = page_text.lower()

    for hint in SORT_SECTION_HINTS:
        index = lower_text.find(hint.lower())
        if index >= 0:
            start = max(index - 120, 0)
            return page_text[start : start + 3200], f"sort_section:{hint}"

    label_indexes = [
        index
        for label in SORT_LABELS
        for index in [lower_text.find(label.lower())]
        if index >= 0
    ]
    if label_indexes:
        start = max(min(label_indexes) - 120, 0)
        return page_text[start : start + 3200], "sort_label"

    return page_text[:6000], "leading_slice"


def get_flight_results_scope(page_text: str) -> str:
    scoped_text, _ = get_flight_results_scope_details(page_text)
    return scoped_text


def match_sort_label(
    line_text: str, labels: tuple[str, ...]
) -> Optional[tuple[str, int]]:
    stripped = line_text.strip()
    lowered = stripped.lower()
    for label in sorted(labels, key=len, reverse=True):
        label_lower = label.lower()
        if lowered == label_lower:
            return label, 3
        if lowered.startswith(label_lower):
            return label, 2
        if lowered.lstrip("•- ").startswith(label_lower):
            return label, 1
    return None


def locate_labeled_price_search(
    page_text: str, labels: tuple[str, ...]
) -> LabeledPriceSearch:
    lines = page_text.splitlines()
    scored_candidates: list[tuple[int, int, int, int, LabeledPriceCandidate]] = []
    matched_labels = 0
    unparsed_blocks: list[str] = []

    for index, raw_line in enumerate(lines):
        matched = match_sort_label(raw_line, labels)
        if not matched:
            continue

        matched_labels += 1
        label, score = matched
        block_parts: list[str] = []
        suffix = raw_line.strip()[len(label) :].strip(" :-\t")
        if suffix:
            block_parts.append(suffix)

        distance = 99
        hint_score = 0
        for offset in range(1, LABEL_PRICE_SCAN_LINES + 1):
            next_index = index + offset
            if next_index >= len(lines):
                break
            next_line = lines[next_index].strip()
            if not next_line:
                continue
            if match_sort_label(next_line, SORT_LABELS):
                break
            if next_line in SORT_SECTION_HINTS:
                continue
            block_parts.append(next_line)
            if any(hint in next_line for hint in PRICE_PREFIX_HINTS):
                hint_score = 1
            if distance == 99 and parse_price_fragment(next_line):
                distance = offset
                break

        if distance == 99:
            distance = 0 if suffix and parse_price_fragment(suffix) else 99
        if any(hint in part for part in block_parts for hint in PRICE_PREFIX_HINTS):
            hint_score = 1

        block_text = "\n".join(block_parts).strip()
        parsed = parse_price_fragment(block_text)
        if not parsed:
            if block_text:
                unparsed_blocks.append(block_text)
            continue

        scored_candidates.append(
            (
                score,
                hint_score,
                distance,
                index,
                LabeledPriceCandidate(
                    label=label,
                    currency=parsed.currency,
                    price=parsed.price,
                    score=score,
                    hint_score=hint_score,
                    distance=distance,
                    line_index=index,
                    raw_block=block_text,
                ),
            )
        )

    scored_candidates.sort(key=lambda item: (-item[0], -item[1], item[2], item[3]))
    return LabeledPriceSearch(
        candidates=tuple(candidate for _, _, _, _, candidate in scored_candidates),
        matched_labels=matched_labels,
        unparsed_blocks=tuple(unparsed_blocks),
    )


def extract_labeled_page_price(
    page_text: str, labels: tuple[str, ...]
) -> Optional[tuple[float, str, str]]:
    candidates = extract_labeled_page_price_candidates(page_text, labels)
    if not candidates:
        return None
    price, currency, label = candidates[0]
    return price, currency, label


def extract_labeled_page_price_candidates(
    page_text: str, labels: tuple[str, ...]
) -> list[tuple[float, str, str]]:
    search = locate_labeled_price_search(page_text, labels)
    return [
        (candidate.price, candidate.currency, candidate.label)
        for candidate in search.candidates
    ]


def merge_price_searches(
    primary: LabeledPriceSearch, fallback: LabeledPriceSearch
) -> LabeledPriceSearch:
    merged: list[LabeledPriceCandidate] = []
    seen: set[LabeledPriceCandidate] = set()
    for candidate in [*primary.candidates, *fallback.candidates]:
        if candidate in seen:
            continue
        seen.add(candidate)
        merged.append(candidate)
    return LabeledPriceSearch(
        candidates=tuple(merged),
        matched_labels=primary.matched_labels + fallback.matched_labels,
        unparsed_blocks=tuple([*primary.unparsed_blocks, *fallback.unparsed_blocks]),
    )


def best_candidate_search_for_region(
    scoped_text: str, region: RegionConfig
) -> LabeledPriceSearch:
    region_labels = REGION_BEST_LABELS.get(region.code, ()) or BEST_LABELS
    primary = locate_labeled_price_search(scoped_text, region_labels)
    if region_labels == BEST_LABELS:
        return primary

    fallback = locate_labeled_price_search(scoped_text, BEST_LABELS)
    return merge_price_searches(primary, fallback)


def best_candidates_for_region(
    scoped_text: str, region: RegionConfig
) -> list[tuple[float, str, str]]:
    return [
        (candidate.price, candidate.currency, candidate.label)
        for candidate in best_candidate_search_for_region(scoped_text, region).candidates
    ]


def recognize_page_state(page_text: str, scoped_text: str, scope_strategy: str) -> PageStateRecognition:
    challenge_hint = find_page_hint(page_text, CHALLENGE_HINTS)
    loading_hint = find_page_hint(page_text, LOADING_HINTS)
    redirect_hint = find_page_hint(page_text, REDIRECT_HINTS)
    unsupported_hint = find_page_hint(page_text, UNSUPPORTED_ROUTE_HINTS)
    no_flights_hint = find_page_hint(page_text, NO_FLIGHTS_HINTS)
    is_blank = len(page_text.strip()) < 100
    scoped_price_found = parse_price_fragment(scoped_text) is not None
    sort_marker_found = find_first_sort_marker(page_text) >= 0

    if challenge_hint and not scoped_price_found:
        state = "challenge"
    elif loading_hint and not scoped_price_found:
        state = "loading"
    elif redirect_hint and not scoped_price_found:
        state = "redirect"
    elif unsupported_hint and not scoped_price_found:
        state = "unsupported"
    elif no_flights_hint and not scoped_price_found:
        state = "no_flights"
    elif is_blank and not scoped_price_found:
        state = "blank"
    elif sort_marker_found or scoped_price_found:
        state = "results"
    else:
        state = "unknown"

    return PageStateRecognition(
        state=state,
        challenge_hint=challenge_hint,
        loading_hint=loading_hint,
        redirect_hint=redirect_hint,
        unsupported_hint=unsupported_hint,
        no_flights_hint=no_flights_hint,
        is_blank=is_blank,
        scope_strategy=scope_strategy,
        sort_marker_found=sort_marker_found,
        scoped_price_found=scoped_price_found,
    )


def extract_page_quote_with_diagnostics(
    region: RegionConfig, source_url: str, page_text: str
) -> tuple[FlightQuote, PageParseDiagnostics]:
    page_text = slice_page_text_for_scan(page_text)
    scoped_text, scope_strategy = get_flight_results_scope_details(page_text)
    state = recognize_page_state(page_text, scoped_text, scope_strategy)
    currency = region.currency

    if state.state == "challenge":
        quote = FlightQuote(
            region=region.code,
            domain=region.domain,
            price=None,
            currency=region.currency,
            source_url=source_url,
            status="page_challenge",
            error=f"页面仍停留在人机验证/安全检查: {state.challenge_hint}",
        )
        diagnostics = PageParseDiagnostics(
            state=state,
            best_candidates=(),
            cheapest_candidates=(),
            fallback_price=None,
            selected_best=None,
            selected_cheapest=None,
            final_status=quote.status,
            validation_outcome="blocked_by_challenge",
            failure_stage="page_state_recognition",
            failure_reason=quote.error,
            used_fallback=False,
        )
        return attach_parser_trust_metadata(quote, diagnostics), diagnostics

    if state.state == "redirect":
        quote = FlightQuote(
            region=region.code,
            domain=region.domain,
            price=None,
            currency=region.currency,
            source_url=source_url,
            status="page_region_redirect",
            error=f"页面发生了区域重定向: {state.redirect_hint}",
        )
        diagnostics = PageParseDiagnostics(
            state=state,
            best_candidates=(),
            cheapest_candidates=(),
            fallback_price=None,
            selected_best=None,
            selected_cheapest=None,
            final_status=quote.status,
            validation_outcome="region_redirect",
            failure_stage="page_state_recognition",
            failure_reason=quote.error,
            used_fallback=False,
        )
        return attach_parser_trust_metadata(quote, diagnostics), diagnostics

    if state.state == "unsupported":
        quote = FlightQuote(
            region=region.code,
            domain=region.domain,
            price=None,
            currency=region.currency,
            source_url=source_url,
            status="page_unsupported_route",
            error=f"该航线在当前区域不支持: {state.unsupported_hint}",
        )
        diagnostics = PageParseDiagnostics(
            state=state,
            best_candidates=(),
            cheapest_candidates=(),
            fallback_price=None,
            selected_best=None,
            selected_cheapest=None,
            final_status=quote.status,
            validation_outcome="unsupported_route",
            failure_stage="page_state_recognition",
            failure_reason=quote.error,
            used_fallback=False,
        )
        return attach_parser_trust_metadata(quote, diagnostics), diagnostics

    if state.state == "no_flights":
        quote = FlightQuote(
            region=region.code,
            domain=region.domain,
            price=None,
            currency=region.currency,
            source_url=source_url,
            status="page_no_flights",
            error=f"页面明确提示无航班结果: {state.no_flights_hint}",
        )
        diagnostics = PageParseDiagnostics(
            state=state,
            best_candidates=(),
            cheapest_candidates=(),
            fallback_price=None,
            selected_best=None,
            selected_cheapest=None,
            final_status=quote.status,
            validation_outcome="no_flights",
            failure_stage="page_state_recognition",
            failure_reason=quote.error,
            used_fallback=False,
        )
        return attach_parser_trust_metadata(quote, diagnostics), diagnostics

    if state.state == "blank":
        quote = FlightQuote(
            region=region.code,
            domain=region.domain,
            price=None,
            currency=region.currency,
            source_url=source_url,
            status="page_empty_shell",
            error="页面内容近乎空白，可能加载失败或被拦截",
        )
        diagnostics = PageParseDiagnostics(
            state=state,
            best_candidates=(),
            cheapest_candidates=(),
            fallback_price=None,
            selected_best=None,
            selected_cheapest=None,
            final_status=quote.status,
            validation_outcome="empty_shell",
            failure_stage="page_state_recognition",
            failure_reason=quote.error,
            used_fallback=False,
        )
        return attach_parser_trust_metadata(quote, diagnostics), diagnostics

    best_labels = REGION_BEST_LABELS.get(region.code, ()) or BEST_LABELS
    cheapest_labels = REGION_CHEAPEST_LABELS.get(region.code, ()) or CHEAPEST_LABELS

    best_search = best_candidate_search_for_region(scoped_text, region)
    cheapest_search = locate_labeled_price_search(scoped_text, cheapest_labels)
    if cheapest_labels != CHEAPEST_LABELS:
        cheapest_search = merge_price_searches(
            cheapest_search,
            locate_labeled_price_search(scoped_text, CHEAPEST_LABELS),
        )

    fallback_price = parse_price_fragment(scoped_text)
    selected_best = best_search.candidates[0] if best_search.candidates else None
    selected_cheapest = (
        cheapest_search.candidates[0] if cheapest_search.candidates else None
    )
    price_candidates = _build_candidate_list(
        page_text,
        region,
        best_search,
        cheapest_search,
        fallback_price,
    )

    if selected_best or selected_cheapest:
        best_price = selected_best.price if selected_best else None
        cheapest_price = selected_cheapest.price if selected_cheapest else None
        best_label = selected_best.label if selected_best else None
        cheapest_label = selected_cheapest.label if selected_cheapest else None
        inconsistency_error = None
        status = "page_text"
        validation_outcome = "passed"
        used_fallback = False

        if (
            best_price is not None
            and cheapest_price is not None
            and best_price < cheapest_price
        ):
            recovered = next(
                (
                    candidate
                    for candidate in best_search.candidates
                    if candidate.price >= cheapest_price
                ),
                None,
            )
            if recovered is not None:
                selected_best = recovered
                best_price = recovered.price
                best_label = recovered.label
                status = "page_text_recovered_best"
                validation_outcome = "recovered_best"
                inconsistency_error = (
                    "Best 初始候选低于 Cheapest，已切换到后续 Best 候选"
                )
                used_fallback = True
            else:
                selected_best = None
                best_price = None
                best_label = None
                status = "page_text_inconsistent"
                validation_outcome = "ignored_inconsistent_best"
                inconsistency_error = (
                    "Best 价格低于 Cheapest，页面文本匹配可能错位，已忽略 Best"
                )
                used_fallback = True
        elif best_price is not None and cheapest_price is None:
            status = "page_text_best_only"
            validation_outcome = "best_only"
            used_fallback = True
        elif cheapest_price is not None and best_price is None:
            status = "page_text_cheapest_only"
            validation_outcome = "cheapest_only"
            used_fallback = True

        primary_price = cheapest_price if cheapest_price is not None else best_price
        primary_label = cheapest_label if cheapest_price is not None else best_label
        quote = FlightQuote(
            region=region.code,
            domain=region.domain,
            price=primary_price,
            currency=currency,
            source_url=source_url,
            status=status,
            price_path=(
                f"document.body.innerText -> {primary_label}"
                if primary_label is not None
                else None
            ),
            best_price=best_price,
            best_price_path=(
                f"document.body.innerText -> {best_label}"
                if best_label is not None
                else None
            ),
            cheapest_price=cheapest_price,
            cheapest_price_path=(
                f"document.body.innerText -> {cheapest_label}"
                if cheapest_label is not None
                else None
            ),
            error=inconsistency_error,
        )
        diagnostics = PageParseDiagnostics(
            state=state,
            best_candidates=best_search.candidates,
            cheapest_candidates=cheapest_search.candidates,
            fallback_price=fallback_price,
            selected_best=selected_best,
            selected_cheapest=selected_cheapest,
            final_status=quote.status,
            validation_outcome=validation_outcome,
            failure_stage=None,
            failure_reason=inconsistency_error,
            used_fallback=used_fallback,
        )
        quote = attach_parser_trust_metadata(quote, diagnostics)
        return attach_price_candidate_metadata(quote, price_candidates), diagnostics

    if fallback_price:
        quote = FlightQuote(
            region=region.code,
            domain=region.domain,
            price=fallback_price.price,
            currency=currency,
            source_url=source_url,
            status="page_text_fallback",
            price_path="document.body.innerText -> first price",
            cheapest_price=fallback_price.price,
            cheapest_price_path="document.body.innerText -> first price",
        )
        diagnostics = PageParseDiagnostics(
            state=state,
            best_candidates=best_search.candidates,
            cheapest_candidates=cheapest_search.candidates,
            fallback_price=fallback_price,
            selected_best=None,
            selected_cheapest=None,
            final_status=quote.status,
            validation_outcome="first_price_fallback",
            failure_stage=None,
            failure_reason=None,
            used_fallback=True,
        )
        quote = attach_parser_trust_metadata(quote, diagnostics)
        return attach_price_candidate_metadata(quote, price_candidates), diagnostics

    selected_candidate = price_candidates[0] if price_candidates else None
    if selected_candidate is not None and selected_candidate.source in {
        "embedded_json_price",
        "script_state_price",
        "visible_text_price",
    }:
        quote = FlightQuote(
            region=region.code,
            domain=region.domain,
            price=selected_candidate.amount,
            currency=currency,
            source_url=source_url,
            status="page_text_embedded_recovered",
            price_path=f"candidate_parser -> {selected_candidate.source}",
            cheapest_price=selected_candidate.amount,
            cheapest_price_path=f"candidate_parser -> {selected_candidate.source}",
        )
        quote.confidence = min(confidence_to_float(selected_candidate.confidence), 0.72)
        quote.price_source = selected_candidate.source
        quote.evidence_text = " ".join(selected_candidate.evidence_text.split())[:240]
        if selected_candidate.confidence != "high":
            quote.parser_warnings.append("候选价格来自恢复解析，需人工确认。")
        diagnostics = PageParseDiagnostics(
            state=state,
            best_candidates=best_search.candidates,
            cheapest_candidates=cheapest_search.candidates,
            fallback_price=fallback_price,
            selected_best=None,
            selected_cheapest=None,
            final_status=quote.status,
            validation_outcome="candidate_recovered",
            failure_stage=None,
            failure_reason=None,
            used_fallback=True,
        )
        return attach_price_candidate_metadata(quote, price_candidates), diagnostics

    if state.loading_hint:
        quote = FlightQuote(
            region=region.code,
            domain=region.domain,
            price=None,
            currency=currency,
            source_url=source_url,
            status="page_loading",
            error=f"页面仍在加载结果: {state.loading_hint}",
        )
        diagnostics = PageParseDiagnostics(
            state=state,
            best_candidates=best_search.candidates,
            cheapest_candidates=cheapest_search.candidates,
            fallback_price=fallback_price,
            selected_best=None,
            selected_cheapest=None,
            final_status=quote.status,
            validation_outcome="loading",
            failure_stage="page_state_recognition",
            failure_reason=quote.error,
            used_fallback=False,
        )
        quote = attach_parser_trust_metadata(quote, diagnostics)
        return attach_price_candidate_metadata(quote, price_candidates), diagnostics

    if (
        (best_search.matched_labels or cheapest_search.matched_labels)
        and not best_search.candidates
        and not cheapest_search.candidates
    ):
        failure_stage = "currency_parse"
        failure_reason = "找到排序标签，但价格/币种解析失败"
    elif state.sort_marker_found:
        failure_stage = "price_block_location"
        failure_reason = "找到结果排序区，但未定位到可解析价格块"
    else:
        failure_stage = "page_state_recognition"
        failure_reason = "页面正文未识别到结果排序区"

    quote = FlightQuote(
        region=region.code,
        domain=region.domain,
        price=None,
        currency=currency,
        source_url=source_url,
        status="page_parse_failed",
        error="页面正文未识别到 Best/Cheapest 价格",
    )
    diagnostics = PageParseDiagnostics(
        state=state,
        best_candidates=best_search.candidates,
        cheapest_candidates=cheapest_search.candidates,
        fallback_price=fallback_price,
        selected_best=None,
        selected_cheapest=None,
        final_status=quote.status,
        validation_outcome="failed",
        failure_stage=failure_stage,
        failure_reason=failure_reason,
        used_fallback=False,
    )
    quote = attach_parser_trust_metadata(quote, diagnostics)
    return attach_price_candidate_metadata(quote, price_candidates), diagnostics


def extract_page_quote(
    region: RegionConfig, source_url: str, page_text: str
) -> FlightQuote:
    quote, _ = extract_page_quote_with_diagnostics(region, source_url, page_text)
    return quote


def page_parse_diagnostics_to_dict(
    diagnostics: PageParseDiagnostics,
) -> dict[str, Any]:
    def candidate_to_dict(candidate: LabeledPriceCandidate) -> dict[str, Any]:
        return {
            "label": candidate.label,
            "currency": candidate.currency,
            "price": candidate.price,
            "score": candidate.score,
            "hint_score": candidate.hint_score,
            "distance": candidate.distance,
            "line_index": candidate.line_index,
            "raw_block": candidate.raw_block,
        }

    fallback = None
    if diagnostics.fallback_price is not None:
        fallback = {
            "currency": diagnostics.fallback_price.currency,
            "price": diagnostics.fallback_price.price,
            "raw_text": diagnostics.fallback_price.raw_text,
            "match_kind": diagnostics.fallback_price.match_kind,
        }

    return {
        "final_status": diagnostics.final_status,
        "validation_outcome": diagnostics.validation_outcome,
        "failure_stage": diagnostics.failure_stage,
        "failure_reason": diagnostics.failure_reason,
        "used_fallback": diagnostics.used_fallback,
        "state": {
            "state": diagnostics.state.state,
            "challenge_hint": diagnostics.state.challenge_hint,
            "loading_hint": diagnostics.state.loading_hint,
            "scope_strategy": diagnostics.state.scope_strategy,
            "sort_marker_found": diagnostics.state.sort_marker_found,
            "scoped_price_found": diagnostics.state.scoped_price_found,
        },
        "best_candidates": [
            candidate_to_dict(candidate)
            for candidate in diagnostics.best_candidates[:5]
        ],
        "cheapest_candidates": [
            candidate_to_dict(candidate)
            for candidate in diagnostics.cheapest_candidates[:5]
        ],
        "fallback_price": fallback,
        "selected_best": (
            candidate_to_dict(diagnostics.selected_best)
            if diagnostics.selected_best is not None
            else None
        ),
        "selected_cheapest": (
            candidate_to_dict(diagnostics.selected_cheapest)
            if diagnostics.selected_cheapest is not None
            else None
        ),
    }
