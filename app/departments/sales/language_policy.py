"""Pure deterministic language and tone selection for customer-facing Sales work."""

from __future__ import annotations

import re

from app.models import SalesLanguage, SalesTone


DEFAULT_SALES_LANGUAGE = SalesLanguage.ENGLISH
DEFAULT_SALES_TONE = SalesTone.PROFESSIONAL


_TUNISIAN_ARABIC_MARKERS = (
    "شنوة",
    "شنو",
    "قداش",
    "نحب",
    "برشا",
    "علاش",
    "توا",
    "باهي",
    "يعطيك الصحة",
)
_TUNISIAN_ARABIZI_MARKER = re.compile(
    r"(?i)\b(?:chneya|chno|kadeh|9adeh|nheb|barsha|3lech|tawa|behi|yaatik|ma3andich|fama)\b"
)
_ARABIC_SCRIPT = re.compile(r"[\u0600-\u06ff]")
_FRENCH_MARKER = re.compile(
    r"(?i)\b(?:bonjour|bonsoir|merci|prix|combien|vous|je|besoin|souhaite|voudrais|est-ce|comment)\b|[àâçéèêëîïôùûüÿœ]"
)
_ENGLISH_MARKER = re.compile(
    r"(?i)\b(?:hello|hi|thanks|price|cost|how|what|need|want|please|can|would)\b"
)


def detect_sales_language(text: str) -> SalesLanguage | None:
    """Classify only clear supported-language signals without AI or network work."""

    normalized = text.strip()
    if not normalized:
        return None
    folded = normalized.casefold()
    if any(marker in folded for marker in _TUNISIAN_ARABIC_MARKERS):
        return SalesLanguage.TUNISIAN_ARABIC
    if _TUNISIAN_ARABIZI_MARKER.search(normalized):
        return SalesLanguage.TUNISIAN_ARABIC
    if _ARABIC_SCRIPT.search(normalized):
        return SalesLanguage.ARABIC
    if _FRENCH_MARKER.search(normalized):
        return SalesLanguage.FRENCH
    if _ENGLISH_MARKER.search(normalized):
        return SalesLanguage.ENGLISH
    return None


def select_sales_language(
    *,
    customer_message: str,
    workspace_preferred_language: SalesLanguage | None = None,
    prior_customer_messages: tuple[str, ...] = (),
) -> SalesLanguage:
    """Apply trusted defaults, current language, history, then a stable fallback."""

    if workspace_preferred_language is not None:
        return workspace_preferred_language
    current_language = detect_sales_language(customer_message)
    if current_language is not None:
        return current_language
    for message in reversed(prior_customer_messages):
        historic_language = detect_sales_language(message)
        if historic_language is not None:
            return historic_language
    return DEFAULT_SALES_LANGUAGE


def select_sales_tone(workspace_preferred_tone: SalesTone | None) -> SalesTone:
    """Use a trusted workspace choice or a stable professional-friendly default."""

    return workspace_preferred_tone or DEFAULT_SALES_TONE


def render_sales_communication_instruction(
    *, language: SalesLanguage,
    tone: SalesTone,
) -> str:
    """Render style-only trusted guidance; commercial facts remain elsewhere."""

    language_label = {
        SalesLanguage.ENGLISH: "English",
        SalesLanguage.FRENCH: "French",
        SalesLanguage.ARABIC: "Arabic",
        SalesLanguage.TUNISIAN_ARABIC: "Tunisian Arabic (Tunisian dialect)",
    }[language]
    tone_label = {
        SalesTone.PROFESSIONAL: "professional, friendly, and courteous",
        SalesTone.FRIENDLY: "friendly and approachable",
        SalesTone.CONCISE: "concise and professional",
    }[tone]
    return (
        f"Respond in {language_label}. Use a {tone_label} Sales tone. "
        "This language and tone guidance changes wording only; preserve all "
        "authoritative commercial facts and policies."
    )
