from __future__ import annotations

from dataclasses import dataclass
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
    scope_strategy: str
    sort_marker_found: bool
    scoped_price_found: bool


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
    scoped_price_found = parse_price_fragment(scoped_text) is not None
    sort_marker_found = find_first_sort_marker(page_text) >= 0

    if challenge_hint and not scoped_price_found:
        state = "challenge"
    elif loading_hint and not scoped_price_found:
        state = "loading"
    elif sort_marker_found or scoped_price_found:
        state = "results"
    else:
        state = "unknown"

    return PageStateRecognition(
        state=state,
        challenge_hint=challenge_hint,
        loading_hint=loading_hint,
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
        return quote, diagnostics

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
        return quote, diagnostics

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
        return quote, diagnostics

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
        return quote, diagnostics

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
    return quote, diagnostics


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
