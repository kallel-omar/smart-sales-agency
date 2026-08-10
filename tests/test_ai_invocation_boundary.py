"""Architecture guard for the central AI invocation boundary.

Concrete provider construction belongs to ``app.services.llm``.  The gateway
is its only application-level consumer; routes, services, and agents must use
``AIInvocationGateway`` instead.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path


APP_ROOT = Path(__file__).resolve().parents[1] / "app"
ALLOWED_BOUNDARY_FILES = frozenset(
    {
        Path("services/llm.py"),
        Path("services/ai_invocation_gateway.py"),
    }
)
CONCRETE_LLM_CONSTRUCTION_NAMES = frozenset(
    {
        "build_llm",
        "DemoLLM",
        "OpenAICompatibleLLM",
    }
)


@dataclass(frozen=True)
class ArchitectureViolation:
    line: int
    message: str


class _ConcreteLLMConstructionVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self._imported_names: set[str] = set()
        self._module_aliases: set[str] = set()
        self.violations: list[ArchitectureViolation] = []

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:  # noqa: N802
        if node.module == "app.services.llm":
            for imported in node.names:
                if imported.name in CONCRETE_LLM_CONSTRUCTION_NAMES:
                    self._imported_names.add(imported.asname or imported.name)
                    self._violate(
                        node.lineno,
                        f"direct import of {imported.name} from app.services.llm",
                    )
        elif node.module == "app.services":
            for imported in node.names:
                if imported.name == "llm":
                    self._module_aliases.add(imported.asname or imported.name)
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:  # noqa: N802
        for imported in node.names:
            if imported.name == "app.services.llm":
                # ``import app.services.llm`` binds ``app`` when it has no
                # alias, so both forms are retained for call resolution.
                self._module_aliases.add(imported.asname or "app")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        name = self._called_construction_name(node.func)
        if name is not None:
            self._violate(node.lineno, f"direct call to {name}")
        self.generic_visit(node)

    def _called_construction_name(self, function: ast.expr) -> str | None:
        if isinstance(function, ast.Name) and function.id in self._imported_names:
            return function.id
        if not isinstance(function, ast.Attribute):
            return None
        if function.attr not in CONCRETE_LLM_CONSTRUCTION_NAMES:
            return None
        root = _attribute_root_name(function)
        if root in self._module_aliases:
            return function.attr
        return None

    def _violate(self, line: int, message: str) -> None:
        self.violations.append(ArchitectureViolation(line=line, message=message))


def _attribute_root_name(node: ast.Attribute) -> str | None:
    value: ast.expr = node
    while isinstance(value, ast.Attribute):
        value = value.value
    return value.id if isinstance(value, ast.Name) else None


def find_unauthorized_llm_construction(source: str) -> list[ArchitectureViolation]:
    """Return direct concrete-LLM construction paths in one Python source file.

    The caller applies the small boundary-file allowlist.  AST inspection means
    comments and docstrings cannot create architecture violations.
    """

    visitor = _ConcreteLLMConstructionVisitor()
    visitor.visit(ast.parse(source))
    return visitor.violations


def production_boundary_violations(app_root: Path = APP_ROOT) -> dict[Path, list[ArchitectureViolation]]:
    violations: dict[Path, list[ArchitectureViolation]] = {}
    for path in sorted(app_root.rglob("*.py")):
        relative_path = path.relative_to(app_root)
        if relative_path in ALLOWED_BOUNDARY_FILES:
            continue
        detected = find_unauthorized_llm_construction(path.read_text(encoding="utf-8-sig"))
        if detected:
            violations[relative_path] = detected
    return violations


def test_production_tree_enforces_central_ai_invocation_boundary():
    assert production_boundary_violations() == {}


def test_only_provider_factory_and_gateway_are_allowed_boundary_files():
    assert ALLOWED_BOUNDARY_FILES == {
        Path("services/llm.py"),
        Path("services/ai_invocation_gateway.py"),
    }
    assert find_unauthorized_llm_construction(
        (APP_ROOT / "services/llm.py").read_text(encoding="utf-8-sig")
    ) == []
    assert [violation.message for violation in find_unauthorized_llm_construction(
        (APP_ROOT / "services/ai_invocation_gateway.py").read_text(encoding="utf-8-sig")
    )] == ["direct import of build_llm from app.services.llm"]


def test_guard_detects_direct_import_and_aliased_call():
    violations = find_unauthorized_llm_construction(
        "from app.services.llm import build_llm as create_client\n"
        "client = create_client(settings)\n"
    )

    assert [violation.message for violation in violations] == [
        "direct import of build_llm from app.services.llm",
        "direct call to create_client",
    ]


def test_guard_detects_module_import_variants_and_concrete_provider_clients():
    violations = find_unauthorized_llm_construction(
        "from app.services import llm as provider\n"
        "provider.build_llm(settings)\n"
        "provider.OpenAICompatibleLLM(settings)\n"
    )

    assert [violation.message for violation in violations] == [
        "direct call to build_llm",
        "direct call to OpenAICompatibleLLM",
    ]


def test_guard_ignores_comments_and_docstrings():
    violations = find_unauthorized_llm_construction(
        '"""Mention build_llm(settings) without constructing a client."""\n'
        "# build_llm(settings) is prohibited outside the gateway.\n"
        "def describe() -> str:\n"
        "    return 'build_llm'\n"
    )

    assert violations == []
