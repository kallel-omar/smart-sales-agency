from app.agents.sales_agent import SalesConversationAgent
from app.models import SalesStage


def test_detects_price_question_without_dependencies():
    agent = object.__new__(SalesConversationAgent)
    assert agent.detect_stage("How much does it cost?") == SalesStage.QUALIFICATION


def test_detects_objection():
    agent = object.__new__(SalesConversationAgent)
    assert agent.detect_stage("This is too expensive") == SalesStage.OBJECTION_HANDLING


def test_detects_closing_intent():
    agent = object.__new__(SalesConversationAgent)
    assert agent.detect_stage("I want to start") == SalesStage.CLOSING
