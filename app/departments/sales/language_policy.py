"""Pure deterministic language, script, and tone selection for Sales work."""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.models import SalesLanguage, SalesTone, SalesWritingScript

DEFAULT_SALES_LANGUAGE = SalesLanguage.ENGLISH
DEFAULT_SALES_TONE = SalesTone.PROFESSIONAL
DEFAULT_TUNISIAN_SCRIPT = SalesWritingScript.LATIN


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
    "سوم",
    "مانجمش",
    "يلزم",
)
_TUNISIAN_ARABIZI_MARKER = re.compile(
    r"(?i)\b(?:"
    r"9adeh|9addeh|9adech|kadeh|"
    r"nheb|n7eb|"
    r"barsha|fama|soum|"
    r"chneya|chnowa|chno|"
    r"kifeh|m3a|3andi|3lech|a5er|bech|tawa|"
    r"ma3andich|behi|yaatik|ynajem|ykhdem"
    r")\b"
)
_ARABIC_SCRIPT = re.compile(r"[\u0600-\u06ff]")
_LATIN_LETTER = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ]")
_FRENCH_MARKER = re.compile(
    r"(?i)\b(?:bonjour|bonsoir|merci|prix|combien|vous|je|besoin|souhaite|voudrais|est-ce|comment|mais|pour|moins|chère)\b|[àâçéèêëîïôùûüÿœ]"
)
_ENGLISH_MARKER = re.compile(
    r"(?i)\b(?:hello|hi|thanks|price|cost|how|what|need|want|please|can|would)\b"
)


@dataclass(frozen=True, slots=True)
class SalesCommunicationStyle:
    """Trusted language/script choice supplied to the Sales prompt."""

    language: SalesLanguage
    script: SalesWritingScript


@dataclass(frozen=True, slots=True)
class SalesScriptConsistencyResult:
    """Conservative deterministic validation seam for future response hardening."""

    is_consistent: bool
    reason_code: str


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


def detect_sales_writing_script(text: str) -> SalesWritingScript | None:
    """Return a writing system only when the customer text makes it clear."""

    normalized = text.strip()
    if not normalized:
        return None
    if _ARABIC_SCRIPT.search(normalized):
        return SalesWritingScript.ARABIC
    if _LATIN_LETTER.search(normalized):
        return SalesWritingScript.LATIN
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


def select_sales_communication_style(
    *,
    customer_message: str,
    workspace_preferred_language: SalesLanguage | None = None,
    workspace_preferred_script: SalesWritingScript | None = None,
    prior_customer_messages: tuple[str, ...] = (),
) -> SalesCommunicationStyle:
    """Select trusted language and the latest clear customer writing system."""

    language = select_sales_language(
        customer_message=customer_message,
        workspace_preferred_language=workspace_preferred_language,
        prior_customer_messages=prior_customer_messages,
    )
    if workspace_preferred_script is not None:
        script = workspace_preferred_script
    else:
        script = detect_sales_writing_script(customer_message)
        if script is None:
            for message in reversed(prior_customer_messages):
                script = detect_sales_writing_script(message)
                if script is not None:
                    break
        if script is None:
            script = (
                SalesWritingScript.ARABIC
                if language is SalesLanguage.ARABIC
                else DEFAULT_TUNISIAN_SCRIPT
            )
    return SalesCommunicationStyle(language=language, script=script)


def select_sales_tone(workspace_preferred_tone: SalesTone | None) -> SalesTone:
    """Use a trusted workspace choice or a stable professional-friendly default."""

    return workspace_preferred_tone or DEFAULT_SALES_TONE


def render_sales_communication_instruction(
    *,
    language: SalesLanguage,
    script: SalesWritingScript = SalesWritingScript.LATIN,
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
    instruction = (
        f"Respond in {language_label}. Use a {tone_label} Sales tone. "
        "This language and tone guidance changes wording only; preserve all "
        "authoritative commercial facts and policies."
    )
    if language is SalesLanguage.TUNISIAN_ARABIC:
        instruction += (
            " Tunisian Arabizi may use numbers as Arabic-sound substitutions "
            "(for example 9adeh, n7eb, 3andi, m3a, and a5er); interpret it as "
            "normal Tunisian conversational text."
        )
        if script is SalesWritingScript.LATIN:
            instruction += (
                " Write Tunisian conversational prose using Latin characters only. "
                "Do not insert Arabic-script Tunisian words. Canonical product and "
                "brand names, URLs, codes, currencies, and technical names may remain "
                "in their authoritative form."
            )
        else:
            instruction += (
                " Write Tunisian conversational prose using Arabic script. Avoid "
                "unnecessary Latin transliteration of Tunisian words. Canonical product "
                "and brand names, URLs, codes, currencies, and technical names may "
                "remain in their authoritative form."
            )
    return instruction


def validate_sales_script_consistency(
    *,
    text: str,
    style: SalesCommunicationStyle,
) -> SalesScriptConsistencyResult:
    """Flag only obvious Tunisian-script mismatches without rewriting a reply."""

    if style.language is not SalesLanguage.TUNISIAN_ARABIC:
        return SalesScriptConsistencyResult(True, "not_applicable")
    arabic_letters = len(_ARABIC_SCRIPT.findall(text))
    latin_words = re.findall(r"(?i)\b[a-z][a-z0-9']*\b", text)
    if style.script is SalesWritingScript.LATIN and arabic_letters >= 3:
        return SalesScriptConsistencyResult(False, "unexpected_arabic_script")
    if (
        style.script is SalesWritingScript.ARABIC
        and arabic_letters == 0
        and len(latin_words) >= 4
    ):
        return SalesScriptConsistencyResult(False, "predominantly_latin_script")
    return SalesScriptConsistencyResult(True, "consistent")
