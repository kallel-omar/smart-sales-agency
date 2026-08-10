"""Pure, provider-neutral prompt composition for Sales agent execution.

The composition keeps trusted instructions separate from untrusted customer
content until the final rendering step required by the current LLM boundary.
It deliberately performs no persistence, network, or model work.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class PromptSectionKind(StrEnum):
    PLATFORM_POLICY = "platform_policy"
    DEPARTMENT_POLICY = "department_policy"
    AGENT_INSTRUCTIONS = "agent_instructions"
    WORKSPACE_INSTRUCTIONS = "workspace_instructions"
    BUSINESS_CONTEXT = "business_context"
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


@dataclass(frozen=True, slots=True)
class PromptMessage:
    """Role-aware message retained before rendering to the current gateway shape."""

    role: PromptMessageRole
    content: str
    trust_level: PromptTrustLevel


@dataclass(frozen=True, slots=True)
class WorkspaceSalesInstructions:
    """Trusted, server-owned workspace Sales instructions when configured."""

    content: str


@dataclass(frozen=True, slots=True)
class SalesBusinessContext:
    """Structured read-model context copied from canonical Sales data at runtime."""

    company_name: str | None = None
    product_catalog: str | None = None
    availability_notes: str | None = None
    policies: tuple[str, ...] = ()

    def render(self) -> str:
        parts: list[str] = []
        if self.company_name:
            parts.append(f"Business: {self.company_name}")
        if self.product_catalog:
            parts.append(f"Product catalog:\n{self.product_catalog}")
        if self.availability_notes:
            parts.append(f"Availability: {self.availability_notes}")
        if self.policies:
            parts.append("Policies:\n" + "\n".join(f"- {policy}" for policy in self.policies))
        return "\n".join(parts)


@dataclass(frozen=True, slots=True)
class PromptCompositionInput:
    """Typed source data for one Sales prompt composition.

    Workspace instructions are intentionally supplied only by trusted
    server-owned configuration.  Task 266 exposes the seam without creating a
    persistence field or accepting any customer-provided instruction value.
    """

    platform_policy: str
    department_policy: str
    agent_instructions: str
    current_task: str
    workspace_instructions: WorkspaceSalesInstructions | None = None
    business_context: SalesBusinessContext | None = None
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
            PromptSection(
                kind=PromptSectionKind.AGENT_INSTRUCTIONS,
                content=source.agent_instructions,
                role=PromptMessageRole.SYSTEM,
                trust_level=PromptTrustLevel.TRUSTED,
            ),
        ]
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
