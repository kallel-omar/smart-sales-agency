"""First governed Sales AgentSkill: ``pricing_explanation:v1``.

The module is deliberately narrow. It owns code-defined selection, transient
typed contracts, deterministic pricing analysis, output validation, and safe
fallback wording. It performs no persistence, provider calls, or side effects.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from uuid import UUID

from app.core.agent_skill_execution import (
    AgentSkillComponentResolver,
    AgentSkillContractRegistry,
    AgentSkillValidatorRegistry,
    ResolvedAgentSkillComponents,
)
from app.core.agent_skills import AgentSkillDefinition
from app.departments.sales.language_policy import (
    SalesCommunicationStyle,
    detect_sales_language,
    validate_sales_script_consistency,
)
from app.models import SalesLanguage, SalesStage, SalesWritingScript

PRICING_EXPLANATION_KEY = "pricing_explanation"
PRICING_EXPLANATION_VERSION = "v1"
PRICING_EXPLANATION_INPUT_CONTRACT = "sales.pricing_explanation.input.v1"
PRICING_EXPLANATION_OUTPUT_CONTRACT = "sales.pricing_explanation.output.v1"
PRICING_EXPLANATION_VALIDATOR = "sales.pricing_explanation.output_validator.v1"
PRICING_EXPLANATION_INSTRUCTION_COMPONENT = "sales.pricing_explanation.instruction.v1"

PRICING_EXPLANATION_INSTRUCTIONS = (
    "Pricing explanation skill v1: Explain only the authoritative active product facts "
    "supplied below. Answer a confirmed price directly and concisely, using the billing "
    "period exactly as supplied. If product choice is ambiguous, ask exactly one useful "
    "clarifying question. Never infer currency, tax, fees, discounts, contract terms, "
    "features, integrations, entitlements, or commercial exceptions. Never promise a "
    "discount or custom deal. Preserve the requested customer language and natural "
    "communication style. Return one JSON object only, without Markdown, with keys "
    "response_text, outcome, pricing_references, escalation_reason, and language. "
    "outcome must be answered, needs_clarification, escalation_required, or "
    "insufficient_verified_pricing. Each pricing_references item must contain only "
    "product_name, price, and billing_period copied exactly from authoritative context."
)

_PRICING_SIGNAL = re.compile(
    r"(?i)(?:\bprice(?:s|d|ing)?\b|\bcost(?:s|ing)?\b|"
    r"\bprix\b|\btarif(?:s)?\b|co[uû]te|"
    r"سعر|السعر|ثمن|الثمن|سوم|التكلفة|"
    r"\bsoum\b|\b9adeh\b|\b9addeh\b|\b9adech\b|\bkadeh\b)"
)
_DISCOUNT_SIGNAL = re.compile(
    r"(?i)(?:\bdiscount\b|\bpercent\s+off\b|\b\d+(?:\.\d+)?\s*%\s*off\b|"
    r"\bremise\b|\br[ée]duction\b|خصم|تخفيض)"
)
_CUSTOM_DEAL_SIGNAL = re.compile(
    r"(?i)(?:\bcustom\s+(?:deal|offer|price|pricing|contract)\b|"
    r"\bspecial\s+(?:deal|offer|price|pricing|terms?)\b|"
    r"\bnegotiat(?:e|ed|ion)\b|\boffer\s+me\s+a\s+deal\b|"
    r"offre\s+(?:sp[ée]ciale|personnalis[ée]e)|tarif\s+personnalis[ée]|"
    r"عرض\s+خاص|سعر\s+خاص|عقد\s+خاص)"
)
_CURRENCY_REQUEST = re.compile(
    r"(?i)(?:\b(?:usd|eur|gbp|dollar(?:s)?|euro(?:s)?|dinar(?:s)?|tnd|dt)\b|"
    r"دولار|يورو|دينار|العملة)"
)
_PRODUCT_FACT_QUESTION = re.compile(
    r"(?i)(?:\binclude(?:s|d)?\b|\bsupport(?:s|ed)?\b|\bintegrat(?:e|es|ed|ion)\b|"
    r"\bfeature(?:s)?\b|\bworks?\s+with\b|\binclut\b|\bint[ée]gration\b|"
    r"يشمل|يتضمن|يدعم|تكامل)"
)
_HIGH_RISK_PRODUCT_TERMS = re.compile(
    r"(?i)\b(?:salesforce|hubspot|crm|erp|slack|zapier|api|integration|feature)\b|"
    r"تكامل|ميزة|يدعم|يشمل"
)
_CURRENCY_IN_REPLY = re.compile(
    r"(?i)(?:[$€£]|\b(?:usd|eur|gbp|dollars?|euros?|dinars?|tnd|dt)\b|"
    r"دولار|يورو|دينار)"
)
_PERCENTAGE = re.compile(r"\b\d+(?:[.,]\d+)?\s*%")
_UNAUTHORIZED_DISCOUNT_PROMISE = re.compile(
    r"(?i)(?:\b(?:i|we)\s+can\s+(?:give|offer|apply)\b.{0,40}\bdiscount\b|"
    r"\byou(?:'ll|\s+will)\s+(?:get|receive)\b.{0,40}\bdiscount\b|"
    r"\bdiscount\s+(?:is|has\s+been)\s+(?:approved|applied)\b|"
    r"je\s+peux\s+vous\s+(?:accorder|offrir).{0,40}(?:remise|r[ée]duction)|"
    r"(?:remise|r[ée]duction).{0,30}(?:accord[ée]e|appliqu[ée]e)|"
    r"يمكنني.{0,30}(?:خصم|تخفيض)|(?:تم|سوف).{0,20}(?:تطبيق|منح).{0,20}(?:خصم|تخفيض))"
)
_PRODUCT_CLAIM_ASSERTION = re.compile(
    r"(?i)(?:\b(?:include(?:s|d)?|support(?:s|ed)?|provide(?:s|d)?|offer(?:s|ed)?|"
    r"enable(?:s|d)?|integrat(?:e|es|ed)|comes?\s+with)\b|"
    r"\b(?:inclut|comprend|offre|permet|int[èe]gre)\b|"
    r"يشمل|يتضمن|يدعم|يوفر)"
)
_NUMBER_TOKEN = re.compile(r"(?<![\w])\d+(?:[.,]\d+)?(?![\w])")
_UNCERTAINTY_MARKER = re.compile(
    r"(?i)(?:cannot\s+confirm|can't\s+confirm|not\s+confirmed|unavailable|"
    r"human\s+(?:review|confirmation)|team\s+member|"
    r"ne\s+peux\s+pas\s+confirmer|pas\s+confirm[ée]|indisponible|"
    r"doit\s+(?:confirmer|v[ée]rifier)|"
    r"لا\s+(?:يمكنني|أستطيع)\s+تأكيد|غير\s+متاح|مراجعة\s+بشرية|"
    r"مانجمش\s+نأكد|يلزم\s+تأكيد)"
)


class PricingEvidenceClassification(StrEnum):
    CONFIRMED = "confirmed"
    INFERENCE = "inference"
    UNKNOWN = "unknown"


class PricingExplanationOutcome(StrEnum):
    ANSWERED = "answered"
    NEEDS_CLARIFICATION = "needs_clarification"
    ESCALATION_REQUIRED = "escalation_required"
    INSUFFICIENT_VERIFIED_PRICING = "insufficient_verified_pricing"


class PricingValidationOutcome(StrEnum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class PricingExplanationContractError(ValueError):
    """Raised when generated pricing output does not satisfy the typed contract."""


class PricingExplanationValidationError(ValueError):
    """Raised when a typed result is not grounded in its authoritative input."""


@dataclass(frozen=True, slots=True)
class PricingProductFact:
    name: str
    description: str
    price: str | None
    billing_period: str | None


@dataclass(frozen=True, slots=True)
class PricingConversationMessage:
    direction: str
    content: str


@dataclass(frozen=True, slots=True)
class PricingExplanationInput:
    workspace_id: UUID
    customer_message: str
    conversation_context: tuple[PricingConversationMessage, ...]
    sales_stage: SalesStage
    products: tuple[PricingProductFact, ...]
    selected_products: tuple[PricingProductFact, ...]
    evidence_classification: PricingEvidenceClassification
    evidence_reason: str
    language: SalesLanguage
    script: SalesWritingScript
    preserve_code_switching: bool
    workspace_instructions: str | None = None


@dataclass(frozen=True, slots=True)
class PricingReference:
    product_name: str
    price: str
    billing_period: str | None = None

    @classmethod
    def from_value(cls, value: object) -> PricingReference:
        if not isinstance(value, dict):
            raise PricingExplanationContractError("Pricing reference must be an object")
        allowed = {"product_name", "price", "billing_period"}
        if set(value) != allowed:
            raise PricingExplanationContractError("Pricing reference fields are invalid")
        product_name = _required_text(value.get("product_name"), "product_name", 200)
        price = _canonical_price(value.get("price"))
        billing_value = value.get("billing_period")
        billing_period = None
        if billing_value is not None:
            billing_period = _required_text(billing_value, "billing_period", 100)
        return cls(product_name, price, billing_period)


@dataclass(frozen=True, slots=True)
class PricingExplanationOutput:
    response_text: str
    outcome: PricingExplanationOutcome
    pricing_references: tuple[PricingReference, ...]
    escalation_reason: str | None
    language: SalesLanguage

    @classmethod
    def from_json(cls, raw: str) -> PricingExplanationOutput:
        normalized = raw.strip()
        if normalized.startswith("```"):
            normalized = re.sub(r"^```(?:json)?\s*|\s*```$", "", normalized, flags=re.IGNORECASE)
        try:
            value = json.loads(normalized)
        except (TypeError, json.JSONDecodeError) as exc:
            raise PricingExplanationContractError(
                "Pricing explanation output must be one JSON object"
            ) from exc
        if not isinstance(value, dict):
            raise PricingExplanationContractError("Pricing explanation output must be an object")
        required = {
            "response_text",
            "outcome",
            "pricing_references",
            "escalation_reason",
            "language",
        }
        if set(value) != required:
            raise PricingExplanationContractError("Pricing explanation output fields are invalid")
        response_text = _required_text(value.get("response_text"), "response_text", 4_000)
        try:
            outcome = PricingExplanationOutcome(value.get("outcome"))
            language = SalesLanguage(value.get("language"))
        except (TypeError, ValueError) as exc:
            raise PricingExplanationContractError(
                "Pricing explanation outcome or language is invalid"
            ) from exc
        references_value = value.get("pricing_references")
        if not isinstance(references_value, list) or len(references_value) > 10:
            raise PricingExplanationContractError("Pricing references must be a bounded list")
        references = tuple(PricingReference.from_value(item) for item in references_value)
        reason_value = value.get("escalation_reason")
        escalation_reason = None
        if reason_value is not None:
            escalation_reason = _required_text(reason_value, "escalation_reason", 500)
        return cls(response_text, outcome, references, escalation_reason, language)


@dataclass(frozen=True, slots=True)
class PricingExplanationValidationResult:
    outcome: PricingValidationOutcome
    reason_code: str


@dataclass(frozen=True, slots=True)
class PricingExplanationExecutionResult:
    response_text: str
    outcome: PricingExplanationOutcome
    validation_outcome: PricingValidationOutcome
    validation_reason: str
    ai_invoked: bool
    escalation_kind: str | None = None


def is_pricing_explanation_turn(customer_message: str) -> bool:
    """Select the one v1 Skill from bounded server-owned commercial signals."""

    return bool(
        _PRICING_SIGNAL.search(customer_message)
        or _DISCOUNT_SIGNAL.search(customer_message)
        or _CUSTOM_DEAL_SIGNAL.search(customer_message)
    )


def canonical_product_facts(products: list[object]) -> tuple[PricingProductFact, ...]:
    """Copy only fields already authoritative in the existing Sales product context."""

    facts: list[PricingProductFact] = []
    for product in products[:10]:
        billing = product.metadata_json.get("billing")
        billing_period = billing.strip() if isinstance(billing, str) and billing.strip() else None
        facts.append(
            PricingProductFact(
                name=product.name,
                description=product.description,
                price=_canonical_price(product.price) if product.price is not None else None,
                billing_period=billing_period,
            )
        )
    return tuple(facts)


def analyze_pricing_evidence(
    customer_message: str,
    products: tuple[PricingProductFact, ...],
) -> tuple[tuple[PricingProductFact, ...], PricingEvidenceClassification, str]:
    """Select authoritative facts conservatively; inference is never returned as fact."""

    if not products:
        return (), PricingEvidenceClassification.UNKNOWN, "catalog_empty"
    folded = customer_message.casefold()
    named = tuple(product for product in products if product.name.casefold() in folded)
    candidates = named or products
    grouped: dict[str, list[PricingProductFact]] = {}
    for product in candidates:
        grouped.setdefault(product.name.casefold(), []).append(product)
    if named and len(grouped) > 1:
        return (), PricingEvidenceClassification.UNKNOWN, "multiple_named_products"
    if not named and len(grouped) > 1:
        return (), PricingEvidenceClassification.UNKNOWN, "ambiguous_product"
    selected = next(iter(grouped.values()))
    distinct = {(item.price, item.billing_period, item.description) for item in selected}
    if len(distinct) > 1:
        return tuple(selected), PricingEvidenceClassification.UNKNOWN, "conflicting_pricing"
    product = selected[0]
    if product.price is None:
        return (product,), PricingEvidenceClassification.UNKNOWN, "price_unavailable"
    if _CURRENCY_REQUEST.search(customer_message):
        return (product,), PricingEvidenceClassification.UNKNOWN, "currency_unavailable"
    if _asks_unsupported_product_fact(customer_message, products):
        return (product,), PricingEvidenceClassification.UNKNOWN, "product_fact_unavailable"
    return (product,), PricingEvidenceClassification.CONFIRMED, "authoritative_product_price"


def commercial_exception_kind(customer_message: str) -> str | None:
    if _DISCOUNT_SIGNAL.search(customer_message):
        return "unsupported_discount"
    if _CUSTOM_DEAL_SIGNAL.search(customer_message):
        return "custom_pricing"
    return None


def preserve_code_switching(customer_message: str) -> bool:
    folded = customer_message.casefold()
    french = bool(re.search(r"\b(?:combien|prix|co[uû]te|bonjour|merci|c'est)\b", folded))
    tunisian = bool(re.search(r"\b(?:el|soum|nheb|n7eb|9adeh|9addeh|kadeh)\b", folded))
    return french and tunisian


class PricingExplanationOutputValidator:
    """Deterministically reject unsupported commercial content before acceptance."""

    def validate(
        self,
        value: object,
        source: PricingExplanationInput | None = None,
    ) -> PricingExplanationOutput:
        if not isinstance(value, PricingExplanationOutput) or source is None:
            raise PricingExplanationValidationError(
                "Pricing validator requires typed output and authoritative input"
            )
        if value.language is not source.language:
            raise PricingExplanationValidationError("Response language is not authorized")
        if value.outcome in {
            PricingExplanationOutcome.ESCALATION_REQUIRED,
            PricingExplanationOutcome.INSUFFICIENT_VERIFIED_PRICING,
        }:
            if not value.escalation_reason:
                raise PricingExplanationValidationError("Escalation requires a safe reason")
        elif value.escalation_reason is not None:
            raise PricingExplanationValidationError("Non-escalation output has escalation metadata")
        authoritative = {
            (product.name, product.price, product.billing_period)
            for product in source.selected_products
            if product.price is not None
        }
        supplied = {
            (reference.product_name, reference.price, reference.billing_period)
            for reference in value.pricing_references
        }
        if not supplied.issubset(authoritative):
            raise PricingExplanationValidationError("Pricing reference is not authoritative")
        if value.outcome is PricingExplanationOutcome.ANSWERED:
            if source.evidence_classification is not PricingEvidenceClassification.CONFIRMED:
                raise PricingExplanationValidationError("Unknown pricing cannot be answered")
            if not supplied:
                raise PricingExplanationValidationError("Answered pricing requires evidence")
        elif supplied:
            raise PricingExplanationValidationError(
                "Non-answered pricing output must not quote a price"
            )
        self._validate_response_text(value, source, supplied)
        return value

    def _validate_response_text(
        self,
        value: PricingExplanationOutput,
        source: PricingExplanationInput,
        supplied: set[tuple[str, str | None, str | None]],
    ) -> None:
        text = value.response_text
        if _CURRENCY_IN_REPLY.search(text):
            raise PricingExplanationValidationError("Currency is not authoritative")
        if _PERCENTAGE.search(text) or _UNAUTHORIZED_DISCOUNT_PROMISE.search(text):
            raise PricingExplanationValidationError("Discount is not authorized")
        question_count = text.count("?") + text.count("؟")
        if question_count > 1 or (
            value.outcome is PricingExplanationOutcome.NEEDS_CLARIFICATION
            and question_count != 1
        ):
            raise PricingExplanationValidationError(
                "Pricing response has an invalid clarification shape"
            )
        if _PRODUCT_CLAIM_ASSERTION.search(text):
            raise PricingExplanationValidationError("Pricing response contains a product assertion")
        detected_language = detect_sales_language(text)
        if source.language is SalesLanguage.TUNISIAN_ARABIC:
            allowed_languages = {SalesLanguage.TUNISIAN_ARABIC}
            if source.script is SalesWritingScript.ARABIC:
                allowed_languages.add(SalesLanguage.ARABIC)
        else:
            allowed_languages = {source.language}
        if detected_language not in allowed_languages:
            raise PricingExplanationValidationError(
                "Response text does not follow the authorized customer language"
            )
        script_result = validate_sales_script_consistency(
            text=text,
            style=SalesCommunicationStyle(
                language=source.language,
                script=source.script,
            ),
        )
        if not script_result.is_consistent:
            raise PricingExplanationValidationError("Response script is not authorized")
        catalog_text = " ".join(
            f"{product.name} {product.description}" for product in source.products
        ).casefold()
        for term in _HIGH_RISK_PRODUCT_TERMS.findall(text):
            normalized = term.casefold() if isinstance(term, str) else str(term).casefold()
            if normalized and normalized not in catalog_text:
                raise PricingExplanationValidationError("Product claim is not authoritative")
        scrubbed = text
        allowed_prices: set[Decimal] = set()
        for product_name, price, _ in supplied:
            scrubbed = re.sub(re.escape(product_name), "", scrubbed, flags=re.IGNORECASE)
            assert price is not None
            allowed_prices.add(Decimal(price))
            if not _contains_price(text, price):
                raise PricingExplanationValidationError("Referenced price is absent from response")
        numeric_tokens = [
            Decimal(match.replace(",", "."))
            for match in _NUMBER_TOKEN.findall(scrubbed)
        ]
        if not set(numeric_tokens).issubset(allowed_prices):
            raise PricingExplanationValidationError("Response contains an unsupported number")
        if len(numeric_tokens) > len(supplied):
            raise PricingExplanationValidationError("Response repeats a confirmed price")
        if (
            source.evidence_classification is PricingEvidenceClassification.UNKNOWN
            and value.outcome is not PricingExplanationOutcome.NEEDS_CLARIFICATION
            and _UNCERTAINTY_MARKER.search(text) is None
        ):
            raise PricingExplanationValidationError("Unknown pricing must state uncertainty")


def pricing_explanation_components(
    definition: AgentSkillDefinition,
) -> ResolvedAgentSkillComponents:
    """Resolve the exact code-owned v1 contract and validator registrations."""

    resolver = AgentSkillComponentResolver(
        AgentSkillContractRegistry(
            (
                (PRICING_EXPLANATION_INPUT_CONTRACT, PricingExplanationInput),
                (PRICING_EXPLANATION_OUTPUT_CONTRACT, PricingExplanationOutput),
            )
        ),
        AgentSkillValidatorRegistry(
            ((PRICING_EXPLANATION_VALIDATOR, PricingExplanationOutputValidator()),)
        ),
    )
    return resolver.resolve(definition)


def safe_pricing_result(
    source: PricingExplanationInput,
    *,
    reason: str | None = None,
    validation_rejected: bool = False,
) -> PricingExplanationExecutionResult:
    """Produce bounded language-aware output for deterministic or rejected cases."""

    exception = commercial_exception_kind(source.customer_message)
    evidence_reason = reason or source.evidence_reason
    if validation_rejected:
        text = _localized_text(source, "cannot_confirm")
        outcome = PricingExplanationOutcome.ESCALATION_REQUIRED
        escalation = "authoritative_information_unavailable"
    elif exception is not None:
        text = _localized_text(source, "commercial_review")
        outcome = PricingExplanationOutcome.ESCALATION_REQUIRED
        escalation = exception
    elif evidence_reason == "ambiguous_product" or evidence_reason == "multiple_named_products":
        text = _localized_text(source, "clarify_product")
        outcome = PricingExplanationOutcome.NEEDS_CLARIFICATION
        escalation = None
    elif source.evidence_classification is PricingEvidenceClassification.CONFIRMED:
        text = _localized_confirmed_price(source)
        outcome = PricingExplanationOutcome.ANSWERED
        escalation = None
    else:
        text = _localized_text(source, "cannot_confirm")
        outcome = PricingExplanationOutcome.ESCALATION_REQUIRED
        escalation = "authoritative_information_unavailable"
    return PricingExplanationExecutionResult(
        response_text=text,
        outcome=outcome,
        validation_outcome=(
            PricingValidationOutcome.REJECTED
            if validation_rejected
            else PricingValidationOutcome.ACCEPTED
        ),
        validation_reason=evidence_reason,
        ai_invoked=False,
        escalation_kind=escalation,
    )


def _localized_confirmed_price(source: PricingExplanationInput) -> str:
    product = source.selected_products[0]
    billing = f" {product.billing_period}" if product.billing_period else ""
    if source.language is SalesLanguage.FRENCH:
        return f"Le prix confirmé de {product.name} est de {product.price}{billing}."
    if source.language is SalesLanguage.ARABIC:
        return f"السعر المؤكد لـ {product.name} هو {product.price}{billing}."
    if source.language is SalesLanguage.TUNISIAN_ARABIC:
        if source.script is SalesWritingScript.ARABIC:
            return f"السوم المؤكد متاع {product.name} هو {product.price}{billing}."
        return f"Soum {product.name} elli met2akked howa {product.price}{billing}."
    return f"The confirmed price for {product.name} is {product.price}{billing}."


def _localized_text(source: PricingExplanationInput, kind: str) -> str:
    messages = {
        SalesLanguage.ENGLISH: {
            "commercial_review": "I can't authorize special pricing. A team member needs to review that request.",
            "clarify_product": "Which product or option would you like the confirmed price for?",
            "cannot_confirm": "I can't confirm that pricing information from the available data. A team member needs to verify it.",
        },
        SalesLanguage.FRENCH: {
            "commercial_review": "Je ne peux pas autoriser un tarif spécial. Un membre de l’équipe doit examiner cette demande.",
            "clarify_product": "Pour quel produit ou quelle option souhaitez-vous obtenir le prix confirmé ?",
            "cannot_confirm": "Je ne peux pas confirmer ce tarif avec les données disponibles. Un membre de l’équipe doit le vérifier.",
        },
        SalesLanguage.ARABIC: {
            "commercial_review": "لا أستطيع اعتماد سعر خاص. يجب أن يراجع أحد أعضاء الفريق هذا الطلب.",
            "clarify_product": "ما المنتج أو الخيار الذي تريد معرفة سعره المؤكد؟",
            "cannot_confirm": "لا أستطيع تأكيد معلومات السعر من البيانات المتاحة. يجب أن يتحقق منها أحد أعضاء الفريق.",
        },
        SalesLanguage.TUNISIAN_ARABIC: {
            "commercial_review": "مانجمش نوافق على soum spécial. يلزم واحد من الفريق يراجع الطلب.",
            "clarify_product": "Chnouwa el produit walla l'option elli t7eb ta3ref soumha confirmé?",
            "cannot_confirm": "مانجمش نأكد السوم بالمعلومات الموجودة. يلزم واحد من الفريق يتثبت.",
        },
    }
    text = messages[source.language][kind]
    if (
        source.language is SalesLanguage.TUNISIAN_ARABIC
        and source.script is SalesWritingScript.ARABIC
    ):
        arabic_messages = {
            "commercial_review": "مانجمش نوافق على سوم خاص. يلزم واحد من الفريق يراجع الطلب.",
            "clarify_product": "شنوة المنتج ولا الاختيار اللي تحب تعرف سومه المؤكد؟",
            "cannot_confirm": "مانجمش نأكد السوم بالمعلومات الموجودة. يلزم واحد من الفريق يتثبت.",
        }
        text = arabic_messages[kind]
    return text


def _asks_unsupported_product_fact(
    customer_message: str,
    products: tuple[PricingProductFact, ...],
) -> bool:
    if _PRODUCT_FACT_QUESTION.search(customer_message) is None:
        return False
    catalog = " ".join(f"{product.name} {product.description}" for product in products).casefold()
    requested_terms = {
        match.casefold()
        for match in _HIGH_RISK_PRODUCT_TERMS.findall(customer_message)
        if isinstance(match, str)
    }
    return bool(requested_terms) and any(term not in catalog for term in requested_terms)


def _contains_price(text: str, canonical: str) -> bool:
    value = Decimal(canonical)
    variants = {canonical, format(value, "f"), format(value.normalize(), "f")}
    return any(re.search(rf"(?<![\w]){re.escape(item)}(?![\w])", text) for item in variants)


def _canonical_price(value: object) -> str:
    try:
        decimal = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise PricingExplanationContractError("Price is invalid") from exc
    if not decimal.is_finite() or decimal < 0:
        raise PricingExplanationContractError("Price is invalid")
    return format(decimal.quantize(Decimal("0.01")), "f")


def _required_text(value: object, field: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise PricingExplanationContractError(f"{field} must be text")
    normalized = value.strip()
    if not normalized or len(normalized) > maximum:
        raise PricingExplanationContractError(f"{field} is invalid")
    return normalized
