from app.departments.sales.conversation_quality import evaluate_conversation_quality
from app.departments.sales.language_policy import SalesCommunicationStyle
from app.departments.sales.prompt_composition import (
    SALES_CONVERSATION_QUALITY_POLICY,
    PromptCompositionInput,
    PromptSectionKind,
    PromptTrustLevel,
    SalesPromptComposer,
)
from app.models import SalesLanguage, SalesWritingScript


def _source(**overrides: object) -> PromptCompositionInput:
    values = {
        "platform_policy": "Platform policy",
        "department_policy": "Sales department policy",
        "agent_instructions": "Sales conversation instructions",
        "current_task": "Customer message: untrusted request",
    }
    values.update(overrides)
    return PromptCompositionInput(**values)


def test_quality_policy_is_a_trusted_composed_section():
    composition = SalesPromptComposer().compose(
        _source(sales_conversation_quality_policy=SALES_CONVERSATION_QUALITY_POLICY)
    )

    quality_section = next(
        section
        for section in composition.sections
        if section.kind is PromptSectionKind.SALES_CONVERSATION_QUALITY_POLICY
    )

    assert quality_section.content == SALES_CONVERSATION_QUALITY_POLICY
    assert quality_section.trust_level is PromptTrustLevel.TRUSTED
    assert quality_section.content in composition.render().system_prompt


def test_quality_evaluator_flags_repeated_question_in_a_multi_turn_sales_context():
    style = SalesCommunicationStyle(
        language=SalesLanguage.ENGLISH,
        script=SalesWritingScript.LATIN,
    )

    evaluation = evaluate_conversation_quality(
        "What is your monthly budget?",
        (
            "Which product are you considering?",
            "What is your monthly budget?",
        ),
        expected_style=style,
    )

    assert evaluation.repeated_question is True
    assert evaluation.excessive_question_load is False
    assert evaluation.script_consistent is True
    assert evaluation.empty_response is False


def test_quality_evaluator_flags_question_load_empty_output_and_script_mismatch():
    latin_tunisian = SalesCommunicationStyle(
        language=SalesLanguage.TUNISIAN_ARABIC,
        script=SalesWritingScript.LATIN,
    )

    overloaded = evaluate_conversation_quality(
        "Nheb nefhem chnowa el besoin? W 9adeh budget?",
        (),
        expected_style=latin_tunisian,
    )
    empty = evaluate_conversation_quality(
        "   ",
        (),
        expected_style=latin_tunisian,
    )
    mismatched = evaluate_conversation_quality(
        "السوم هو 99 دينار كل شهر",
        (),
        expected_style=latin_tunisian,
    )

    assert overloaded.excessive_question_load is True
    assert overloaded.script_consistent is True
    assert empty.empty_response is True
    assert mismatched.script_consistent is False
