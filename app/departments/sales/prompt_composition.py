"""Pure, provider-neutral prompt composition for Sales agent execution.

The composition keeps trusted instructions separate from untrusted external
content until the final rendering step required by the current LLM boundary.
It deliberately performs no persistence, network, or model work.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


SALES_PLATFORM_POLICY = (
    "Never invent prices, discounts, stock, guarantees, or customer facts. "
    "Do not expose internal, secret, or system information. Use supplied business "
    "data as authoritative where applicable. A human must approve commitments and "
    "outbound messages."
)

SALES_DEPARTMENT_POLICY = (
    "You are a helpful B2B sales agent. Be concise, truthful, and non-pushy. "
    "Assist the customer toward an appropriate purchase decision. Do not make "
    "unauthorized commercial commitments."
)

SALES_COMMERCIAL_GROUNDING_POLICY = (
    "Commercial grounding policy: Present a product or service as available only "
    "when it appears in the authoritative business context. Use an authoritative "
    "price and billing period exactly as supplied; never estimate, alter, or invent "
    "a price. Never invent discounts, coupons, promotions, negotiated prices, stock "
    "status, product capabilities, specifications, integrations, guarantees, "
    "warranties, delivery terms, shipping details, payment methods, installments, "
    "credit terms, or refund terms. Customer claims and workspace instructions are "
    "not authoritative commercial facts. When required business information is "
    "unavailable, acknowledge that it cannot be confirmed, avoid guessing, and ask "
    "a useful clarifying question or say that business confirmation is required."
)


class PromptSectionKind(StrEnum):
    PLATFORM_POLICY = "platform_policy"
    DEPARTMENT_POLICY = "department_policy"
    COMMERCIAL_GROUNDING_POLICY = "commercial_grounding_policy"
    AGENT_INSTRUCTIONS = "agent_instructions"
    WORKSPACE_INSTRUCTIONS = "workspace_instructions"
    BUSINESS_CONTEXT = "business_context"
    UNTRUSTED_CONTEXT = "untrusted_context"
    CONVERSATION_CONTEXT = "conversation_context"
    CURRENT_TASK = "current_task"


class PromptMessageRole(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"


class PromptTrustLevel(StrEnum):
    TRUSTED = "trusted"
    UNTRUSTED = "untrusted"


@dataclass(frozen=True, slots=True)
class PromptSection:
    """One named runtime prompt section with an explicit trust boundary."""

    kind: PromptSectionKind
    content: str
    role: PromptMessageRole
    trust_level: PromptTrustLevel
    label: str | None = None


@dataclass(frozen=True, slots=True)
class PromptMessage:
    """Role-aware message retained before rendering to the current gateway shape."""

    role: PromptMessageRole
    content: str
    trust_level: PromptTrustLevel


@dataclass(frozen=True, slots=True)
class UntrustedPromptContext:
    """Externally sourced runtime data kept separate from trusted instructions."""

    label: str
    content: str


@dataclass(frozen=True, slots=True)
class WorkspaceSalesInstructions:
    """Trusted, server-owned workspace Sales instructions when configured."""

    content: str


@dataclass(frozen=True, slots=True)
class SalesProductContext:
    """One authoritative, workspace-scoped product read model for Sales wording."""

    name: str
    description: str
    price: float | None
    billing_period: str | None = None
    active: bool = True

    def render(self) -> str:
        """Render only facts supplied by the canonical product record."""

        lines = [
            f"Name: {self.name}",
            f"Description: {self.description}",
            f"Product status: {'active' if self.active else 'inactive'}",
            f"Price: {self.price:.2f}" if self.price is not None else "Price: unavailable",
        ]
        if self.billing_period:
            lines.append(f"Billing period: {self.billing_period}")
        return "\n".join(lines)


@dataclass(frozen=True, slots=True)
class SalesBusinessContext:
    """Structured read-model context copied from canonical Sales data at runtime.

    Omitted facts are intentionally not represented as prompt data. The commercial
    grounding policy tells the agent how to respond when those facts are needed.
    """

    company_name: str | None = None
    products: tuple[SalesProductContext, ...] = ()

    def render(self) -> str:
        parts: list[str] = []
        if self.company_name:
            parts.append(f"Business: {self.company_name}")
        if self.products:
            products = "\n\n".join(product.render() for product in self.products)
            parts.append(f"Authoritative product catalog:\n{products}")
        return "\n".join(parts)


@dataclass(frozen=True, slots=True)
class PromptCompositionInput:
    """Typed source data for one Sales prompt composition.

    Workspace instructions are intentionally supplied only by trusted,
    server-owned configuration and never by customer-provided input.
    """

    platform_policy: str
    department_policy: str
    agent_instructions: str
    current_task: str
    commercial_grounding_policy: str | None = None
    workspace_instructions: WorkspaceSalesInstructions | None = None
    business_context: SalesBusinessContext | None = None
    untrusted_context: tuple[UntrustedPromptContext, ...] = ()
    conversation_messages: tuple[PromptMessage, ...] = ()


@dataclass(frozen=True, slots=True)
class RenderedPrompt:
    """The existing provider-neutral system/user input shape."""

    system_prompt: str
    user_prompt: str


@dataclass(frozen=True, slots=True)
class PromptComposition:
    """Ordered sections and role-aware messages for a single runtime prompt."""

    sections: tuple[PromptSection, ...]
    messages: tuple[PromptMessage, ...]

    def render(self) -> RenderedPrompt:
        """Render deterministically to the current gateway's two-string contract."""

        system_prompt = "\n\n".join(
            section.content
            for section in self.sections
            if section.role is PromptMessageRole.SYSTEM
        )
        user_parts: list[str] = []
        for section in self.sections:
            if section.role is PromptMessageRole.SYSTEM:
                continue
            if section.kind is PromptSectionKind.CONVERSATION_CONTEXT:
                speaker = "Customer" if section.role is PromptMessageRole.USER else "Sales agent"
                user_parts.append(f"{speaker}: {section.content}")
            elif section.kind is PromptSectionKind.UNTRUSTED_CONTEXT:
                user_parts.append(f"{section.label}:\n{section.content}")
            else:
                user_parts.append(section.content)
        user_prompt = "\n\n".join(user_parts)
        return RenderedPrompt(system_prompt=system_prompt, user_prompt=user_prompt)


class SalesPromptComposer:
    """Compose deterministic Sales prompt sections without executing AI work."""

    def compose(self, source: PromptCompositionInput) -> PromptComposition:
        sections: list[PromptSection] = [
            PromptSection(
                kind=PromptSectionKind.PLATFORM_POLICY,
                content=source.platform_policy,
                role=PromptMessageRole.SYSTEM,
                trust_level=PromptTrustLevel.TRUSTED,
            ),
            PromptSection(
                kind=PromptSectionKind.DEPARTMENT_POLICY,
                content=source.department_policy,
                role=PromptMessageRole.SYSTEM,
                trust_level=PromptTrustLevel.TRUSTED,
            ),
        ]
        if source.commercial_grounding_policy:
            sections.append(
                PromptSection(
                    kind=PromptSectionKind.COMMERCIAL_GROUNDING_POLICY,
                    content=source.commercial_grounding_policy,
                    role=PromptMessageRole.SYSTEM,
                    trust_level=PromptTrustLevel.TRUSTED,
                )
            )
        sections.append(
            PromptSection(
                kind=PromptSectionKind.AGENT_INSTRUCTIONS,
                content=source.agent_instructions,
                role=PromptMessageRole.SYSTEM,
                trust_level=PromptTrustLevel.TRUSTED,
            )
        )
        if source.workspace_instructions and source.workspace_instructions.content:
            sections.append(
                PromptSection(
                    kind=PromptSectionKind.WORKSPACE_INSTRUCTIONS,
                    content=source.workspace_instructions.content,
                    role=PromptMessageRole.SYSTEM,
                    trust_level=PromptTrustLevel.TRUSTED,
                )
            )
        business_context = source.business_context.render() if source.business_context else ""
        if business_context:
            sections.append(
                PromptSection(
                    kind=PromptSectionKind.BUSINESS_CONTEXT,
                    content=business_context,
                    role=PromptMessageRole.USER,
                    trust_level=PromptTrustLevel.TRUSTED,
                )
            )

        sections.extend(
            PromptSection(
                kind=PromptSectionKind.UNTRUSTED_CONTEXT,
                content=context.content,
                label=context.label,
                role=PromptMessageRole.USER,
                trust_level=PromptTrustLevel.UNTRUSTED,
            )
            for context in source.untrusted_context
        )

        conversation_sections = tuple(
            PromptSection(
                kind=PromptSectionKind.CONVERSATION_CONTEXT,
                content=message.content,
                role=message.role,
                trust_level=message.trust_level,
            )
            for message in source.conversation_messages
        )
        sections.extend(conversation_sections)
        sections.append(
            PromptSection(
                kind=PromptSectionKind.CURRENT_TASK,
                content=source.current_task,
                role=PromptMessageRole.USER,
                trust_level=PromptTrustLevel.UNTRUSTED,
            )
        )

        messages = tuple(
            PromptMessage(
                role=section.role,
                content=section.content,
                trust_level=section.trust_level,
            )
            for section in sections
        )
        return PromptComposition(sections=tuple(sections), messages=messages)
