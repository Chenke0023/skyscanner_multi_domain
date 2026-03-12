from __future__ import annotations

import re
from typing import Any, Optional

from skyscanner_models import FlightQuote, RegionConfig


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


def parse_price_text(text: str) -> Optional[tuple[str, float]]:
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
            return prefix_match.group(1), amount

    suffix_match = re.search(
        rf"({amount_pattern})[ \t\u00a0]*({token_pattern})",
        text,
        re.IGNORECASE,
    )
    if suffix_match:
        amount = parse_float(suffix_match.group(1))
        if amount is not None:
            return suffix_match.group(2), amount

    return None


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


def get_flight_results_scope(page_text: str) -> str:
    page_text = slice_page_text_for_scan(page_text)
    lower_text = page_text.lower()

    for hint in SORT_SECTION_HINTS:
        index = lower_text.find(hint.lower())
        if index >= 0:
            start = max(index - 120, 0)
            return page_text[start : start + 3200]

    label_indexes = [
        index for label in SORT_LABELS for index in [lower_text.find(label.lower())] if index >= 0
    ]
    if label_indexes:
        start = max(min(label_indexes) - 120, 0)
        return page_text[start : start + 3200]

    return page_text[:6000]


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
    lines = page_text.splitlines()
    candidates: list[tuple[int, int, int, int, float, str, str]] = []

    for index, raw_line in enumerate(lines):
        matched = match_sort_label(raw_line, labels)
        if not matched:
            continue

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
            if distance == 99 and parse_price_text(next_line):
                distance = offset
                break

        if distance == 99:
            distance = 0 if suffix and parse_price_text(suffix) else 99
        if any(hint in part for part in block_parts for hint in PRICE_PREFIX_HINTS):
            hint_score = 1

        parsed = parse_price_text("\n".join(block_parts))
        if not parsed:
            continue

        currency, price = parsed
        candidates.append((score, hint_score, distance, index, price, currency, label))

    if not candidates:
        return []

    candidates.sort(key=lambda item: (-item[0], -item[1], item[2], item[3]))
    return [(price, currency, label) for _, _, _, _, price, currency, label in candidates]


def best_candidates_for_region(
    scoped_text: str, region: RegionConfig
) -> list[tuple[float, str, str]]:
    region_labels = REGION_BEST_LABELS.get(region.code, ()) or BEST_LABELS
    primary = extract_labeled_page_price_candidates(scoped_text, region_labels)
    if region_labels == BEST_LABELS:
        return primary

    fallback = extract_labeled_page_price_candidates(scoped_text, BEST_LABELS)
    merged: list[tuple[float, str, str]] = []
    seen: set[tuple[float, str, str]] = set()
    for candidate in [*primary, *fallback]:
        if candidate in seen:
            continue
        seen.add(candidate)
        merged.append(candidate)
    return merged


def extract_page_quote(
    region: RegionConfig, source_url: str, page_text: str
) -> FlightQuote:
    page_text = slice_page_text_for_scan(page_text)

    challenge_hint = find_page_hint(page_text, CHALLENGE_HINTS)
    if challenge_hint:
        return FlightQuote(
            region=region.code,
            domain=region.domain,
            price=None,
            currency=region.currency,
            source_url=source_url,
            status="page_challenge",
            error=f"页面仍停留在人机验证/安全检查: {challenge_hint}",
        )

    loading_hint = find_page_hint(page_text, LOADING_HINTS)
    if loading_hint:
        return FlightQuote(
            region=region.code,
            domain=region.domain,
            price=None,
            currency=region.currency,
            source_url=source_url,
            status="page_loading",
            error=f"页面仍在加载结果: {loading_hint}",
        )

    currency = region.currency
    scoped_text = get_flight_results_scope(page_text)

    best_labels = REGION_BEST_LABELS.get(region.code, ()) or BEST_LABELS
    cheapest_labels = REGION_CHEAPEST_LABELS.get(region.code, ()) or CHEAPEST_LABELS
    best_candidates = best_candidates_for_region(scoped_text, region)
    best_match = best_candidates[0] if best_candidates else None
    if best_match is None and best_labels != BEST_LABELS:
        best_match = extract_labeled_page_price(scoped_text, BEST_LABELS)
    cheapest_match = extract_labeled_page_price(scoped_text, cheapest_labels)
    if cheapest_match is None and cheapest_labels != CHEAPEST_LABELS:
        cheapest_match = extract_labeled_page_price(scoped_text, CHEAPEST_LABELS)
    if best_match or cheapest_match:
        best_price = best_match[0] if best_match else None
        cheapest_price = cheapest_match[0] if cheapest_match else None
        best_label = best_match[2] if best_match else None
        cheapest_label = cheapest_match[2] if cheapest_match else None
        inconsistency_error = None
        status = "page_text"
        if (
            best_price is not None
            and cheapest_price is not None
            and best_price < cheapest_price
        ):
            recovered = next(
                (
                    candidate
                    for candidate in best_candidates
                    if candidate[0] >= cheapest_price
                ),
                None,
            )
            if recovered is not None:
                best_price, _, best_label = recovered
                status = "page_text_recovered_best"
                inconsistency_error = (
                    "Best 初始候选低于 Cheapest，已切换到后续 Best 候选"
                )
            else:
                inconsistency_error = (
                    "Best 价格低于 Cheapest，页面文本匹配可能错位，已忽略 Best"
                )
                best_price = None
                best_label = None
                status = "page_text_inconsistent"
        elif best_price is not None and cheapest_price is None:
            status = "page_text_best_only"
        elif cheapest_price is not None and best_price is None:
            status = "page_text_cheapest_only"
        primary_price = cheapest_price if cheapest_price is not None else best_price
        primary_label = cheapest_label if cheapest_price is not None else best_label
        return FlightQuote(
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

    fallback = parse_price_text(scoped_text)
    if fallback:
        return FlightQuote(
            region=region.code,
            domain=region.domain,
            price=fallback[1],
            currency=currency,
            source_url=source_url,
            status="page_text_fallback",
            price_path="document.body.innerText -> first price",
            cheapest_price=fallback[1],
            cheapest_price_path="document.body.innerText -> first price",
        )

    return FlightQuote(
        region=region.code,
        domain=region.domain,
        price=None,
        currency=currency,
        source_url=source_url,
        status="page_parse_failed",
        error="页面正文未识别到 Best/Cheapest 价格",
    )
