"""
Compatibility import for the previous Sales agent package.

New code should import AgentContext from:
app.departments.sales.agents.base
"""

from app.departments.sales.agents.base import AgentContext

__all__ = ["AgentContext"]