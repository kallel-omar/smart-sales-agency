"""Safe AgentSkill execution identity and future component-resolution seams."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from app.core.agent_skills import AgentSkillDefinition
from app.core.ai_employees import AIEmployeeRoleKey
from app.core.ai_execution_attribution import AIExecutionAttribution
from app.core.capabilities import BusinessCapabilityKey
from app.core.events import Department

_COMPONENT_IDENTIFIER_PATTERN = re.compile(r"^[a-z][a-z0-9_.]{0,199}$")


class AgentSkillComponentRegistryError(ValueError):
    """Raised when a code-owned execution component registration is invalid."""


class DuplicateAgentSkillComponentError(ValueError):
    """Raised when an execution component identifier is already registered."""


class AgentSkillContractNotFoundError(LookupError):
    """Raised when an input or output contract identifier is not registered."""


class AgentSkillValidatorNotFoundError(LookupError):
    """Raised when an output validator identifier is not registered."""


class AgentSkillOutputValidator(Protocol):
    """Future deterministic output validator boundary; 296C never invokes it."""

    def validate(self, value: object) -> object: ...


class AgentSkillContractRegistry:
    """Resolve application-owned typed contract classes by exact identifier."""

    def __init__(self, contracts: Iterable[tuple[str, type[object]]] = ()) -> None:
        self._contracts: dict[str, type[object]] = {}
        for identifier, contract in contracts:
            self.register(identifier, contract)

    def register(self, identifier: str, contract: type[object]) -> None:
        _require_component_identifier(identifier)
        if not isinstance(contract, type):
            raise AgentSkillComponentRegistryError(
                "AgentSkill contract must be a code-owned type"
            )
        if identifier in self._contracts:
            raise DuplicateAgentSkillComponentError(
                "AgentSkill contract identifier is already registered"
            )
        self._contracts[identifier] = contract

    def resolve(self, identifier: str) -> type[object]:
        contract = self._contracts.get(identifier)
        if contract is None:
            raise AgentSkillContractNotFoundError(
                "AgentSkill contract identifier is not registered"
            )
        return contract


class AgentSkillValidatorRegistry:
    """Resolve code-owned validators without invoking skill behavior."""

    def __init__(
        self,
        validators: Iterable[tuple[str, AgentSkillOutputValidator]] = (),
    ) -> None:
        self._validators: dict[str, AgentSkillOutputValidator] = {}
        for identifier, validator in validators:
            self.register(identifier, validator)

    def register(self, identifier: str, validator: AgentSkillOutputValidator) -> None:
        _require_component_identifier(identifier)
        if not callable(getattr(validator, "validate", None)):
            raise AgentSkillComponentRegistryError(
                "AgentSkill validator must implement the validator contract"
            )
        if identifier in self._validators:
            raise DuplicateAgentSkillComponentError(
                "AgentSkill validator identifier is already registered"
            )
        self._validators[identifier] = validator

    def resolve(self, identifier: str) -> AgentSkillOutputValidator:
        validator = self._validators.get(identifier)
        if validator is None:
            raise AgentSkillValidatorNotFoundError(
                "AgentSkill validator identifier is not registered"
            )
        return validator


@dataclass(frozen=True, slots=True)
class ResolvedAgentSkillComponents:
    """Code-owned components resolved before a future skill may execute."""

    input_contract: type[object]
    output_contract: type[object]
    validator: AgentSkillOutputValidator


class AgentSkillComponentResolver:
    """Fail closed when a definition references an unknown execution component."""

    def __init__(
        self,
        contracts: AgentSkillContractRegistry,
        validators: AgentSkillValidatorRegistry,
    ) -> None:
        self.contracts = contracts
        self.validators = validators

    def resolve(self, definition: AgentSkillDefinition) -> ResolvedAgentSkillComponents:
        return ResolvedAgentSkillComponents(
            input_contract=self.contracts.resolve(definition.input_contract),
            output_contract=self.contracts.resolve(definition.output_contract),
            validator=self.validators.resolve(definition.validator),
        )


@dataclass(frozen=True, slots=True)
class AgentSkillExecutionContext:
    """Safe immutable identity for a future, already-authorized skill execution."""

    workspace_id: UUID
    department_id: UUID
    department: Department
    work_item_id: UUID
    ai_employee_id: UUID
    employee_role: AIEmployeeRoleKey
    assignment_id: UUID
    capability_id: UUID
    capability: BusinessCapabilityKey
    skill_key: str
    skill_version: str
    input_contract: str
    output_contract: str
    validator: str
    instruction_component: str
    effective_tool_ceiling: frozenset[str]
    attribution_identifier: str

    @property
    def ai_execution_attribution(self) -> AIExecutionAttribution:
        """Reuse existing workspace-validated AI invocation attribution."""

        return AIExecutionAttribution(
            department_id=self.department_id,
            ai_employee_id=self.ai_employee_id,
            capability_id=self.capability_id,
            work_item_id=self.work_item_id,
        )


def _require_component_identifier(identifier: object) -> None:
    if (
        not isinstance(identifier, str)
        or _COMPONENT_IDENTIFIER_PATTERN.fullmatch(identifier) is None
    ):
        raise AgentSkillComponentRegistryError(
            "AgentSkill component identifier is invalid"
        )
