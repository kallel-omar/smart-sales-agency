"""
Compatibility import for the previous Sales agent package.

New code should import LeadResearchAgent from:
app.departments.sales.agents.lead_researcher
"""

from app.departments.sales.agents.lead_researcher import LeadResearchAgent

__all__ = ["LeadResearchAgent"]