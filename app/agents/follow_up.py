"""
Compatibility import for the previous Sales agent package.

New code should import FollowUpAgent from:
app.departments.sales.agents.follow_up
"""

from app.departments.sales.agents.follow_up import FollowUpAgent

__all__ = ["FollowUpAgent"]