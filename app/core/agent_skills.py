"""Immutable, non-executing AgentSkill definitions and exact registry lookup."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from app.core.ai_employees import AIEmployeeRoleKey
from app.core.capabilities import BusinessCapabilityKey
from app.core.events import Department

_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,99}$")
_VERSION_PATTERN = re.compile(r"^v[1-9][0-9]*$")
_IDENTIFIER_PATTERN = re.compile(r"^[a-z][a-z0-9_.]{0,199}$")


class AgentSkillDefinitionError(ValueError):
    """Raised when an application-owned skill definition is invalid."""


class DuplicateAgentSkillDefinitionError(ValueError):
    """Raised when an exact skill key and version is already registered."""


class AgentSkillNotFoundError(LookupError):
    """Raised when a skill key is not registered."""


class AgentSkillVersionNotFoundError(LookupError):
    """Raised when a skill key exists but the requested version does not."""


class AgentSkillDepartmentNotEligibleError(PermissionError):
    """Raised when a skill does not apply to the requested Department."""


class AgentSkillRoleNotEligibleError(PermissionError):
    """Raised when an AIEmployee role is not eligible for a skill."""


class AgentSkillCapabilityNotEligibleError(PermissionError):
    """Raised when a skill does not belong to the requested Capability."""


@dataclass(frozen=True, slots=True)
class AgentSkillDefinition:
    """Application-owned procedure metadata beneath a BusinessCapability.

    A definition is descriptive only. It cannot assign a capability, grant a
    tool, change autonomy, execute code, or create domain records.
    """

    key: str
    version: str
    department: Department
    eligible_roles: frozenset[AIEmployeeRoleKey]
    required_capability: BusinessCapabilityKey
    input_contract: str
    output_contract: str
    allowed_tool_ceiling: frozenset[str]
    validator: str

    def __post_init__(self) -> None:
        self._require_identifier(self.key, "AgentSkill key", _KEY_PATTERN)
        self._require_identifier(self.version, "AgentSkill version", _VERSION_PATTERN)
        self._require_identifier(
            self.input_contract,
            "AgentSkill input contract",
            _IDENTIFIER_PATTERN,
        )
        self._require_identifier(
            self.output_contract,
            "AgentSkill output contract",
            _IDENTIFIER_PATTERN,
        )
        self._require_identifier(
            self.validator,
            "AgentSkill validator",
            _IDENTIFIER_PATTERN,
        )
        if not isinstance(self.department, Department):
            raise AgentSkillDefinitionError(
                "AgentSkill department must use the canonical Department contract"
            )
        if not self.eligible_roles or any(
            not isinstance(role, AIEmployeeRoleKey) for role in self.eligible_roles
        ):
            raise AgentSkillDefinitionError(
                "AgentSkill eligible roles must use canonical AIEmployee roles"
            )
        if not isinstance(self.required_capability, BusinessCapabilityKey):
            raise AgentSkillDefinitionError(
                "AgentSkill capability must use the canonical BusinessCapability contract"
            )
        if not isinstance(self.allowed_tool_ceiling, frozenset):
            raise AgentSkillDefinitionError("AgentSkill tool ceiling must be immutable")
        for tool in self.allowed_tool_ceiling:
            self._require_identifier(
                tool,
                "AgentSkill tool ceiling entry",
                _KEY_PATTERN,
            )

    @staticmethod
    def _require_identifier(value: object, label: str, pattern: re.Pattern[str]) -> None:
        if not isinstance(value, str) or pattern.fullmatch(value) is None:
            raise AgentSkillDefinitionError(f"{label} is invalid")


class AgentSkillRegistry:
    """Register and resolve exact immutable skill definitions, fail closed."""

    def __init__(self, definitions: Iterable[AgentSkillDefinition] = ()) -> None:
        self._definitions: dict[tuple[str, str], AgentSkillDefinition] = {}
        for definition in definitions:
            self.register(definition)

    def register(self, definition: AgentSkillDefinition) -> None:
        if not isinstance(definition, AgentSkillDefinition):
            raise AgentSkillDefinitionError("AgentSkill registry accepts definitions only")
        identity = (definition.key, definition.version)
        if identity in self._definitions:
            raise DuplicateAgentSkillDefinitionError(
                "AgentSkill key and version is already registered"
            )
        self._definitions[identity] = definition

    def resolve(self, key: str, version: str) -> AgentSkillDefinition:
        definition = self._definitions.get((key, version))
        if definition is not None:
            return definition
        if any(registered_key == key for registered_key, _ in self._definitions):
            raise AgentSkillVersionNotFoundError("AgentSkill version is not registered")
        raise AgentSkillNotFoundError("AgentSkill is not registered")

    def require_eligible(
        self,
        key: str,
        version: str,
        *,
        department: Department,
        role: AIEmployeeRoleKey,
        capability: BusinessCapabilityKey,
    ) -> AgentSkillDefinition:
        definition = self.resolve(key, version)
        try:
            canonical_department = Department(department)
        except (TypeError, ValueError) as exc:
            raise AgentSkillDepartmentNotEligibleError(
                "AgentSkill is not eligible for this Department"
            ) from exc
        if definition.department is not canonical_department:
            raise AgentSkillDepartmentNotEligibleError(
                "AgentSkill is not eligible for this Department"
            )
        try:
            canonical_role = AIEmployeeRoleKey(role)
        except (TypeError, ValueError) as exc:
            raise AgentSkillRoleNotEligibleError(
                "AIEmployee role is not eligible for this AgentSkill"
            ) from exc
        if canonical_role not in definition.eligible_roles:
            raise AgentSkillRoleNotEligibleError(
                "AIEmployee role is not eligible for this AgentSkill"
            )
        try:
            canonical_capability = BusinessCapabilityKey(capability)
        except (TypeError, ValueError) as exc:
            raise AgentSkillCapabilityNotEligibleError(
                "AgentSkill does not belong to this BusinessCapability"
            ) from exc
        if definition.required_capability is not canonical_capability:
            raise AgentSkillCapabilityNotEligibleError(
                "AgentSkill does not belong to this BusinessCapability"
            )
        return definition

    def list_definitions(self) -> tuple[AgentSkillDefinition, ...]:
        """Return definitions in stable identity order."""

        return tuple(
            definition
            for _, definition in sorted(
                self._definitions.items(),
                key=lambda item: item[0],
            )
        )


def effective_agent_skill_tools(
    authorized_tools: Iterable[str],
    definition: AgentSkillDefinition,
) -> frozenset[str]:
    """Intersect existing authorization with a skill ceiling; never grant access."""

    return frozenset(authorized_tools).intersection(definition.allowed_tool_ceiling)
