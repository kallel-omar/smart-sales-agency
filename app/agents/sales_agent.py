"""
Compatibility import for the previous Sales agent package.

New code should import SalesConversationAgent from:
app.departments.sales.agents.sales_agent
"""

from app.departments.sales.agents.sales_agent import SalesConversationAgent

__all__ = ["SalesConversationAgent"]