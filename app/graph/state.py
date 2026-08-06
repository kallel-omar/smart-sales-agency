"""
Compatibility import for the previous graph package.

New code should import SalesWorkflowState from:
app.departments.sales.workflows.state
"""

from app.departments.sales.workflows.state import (
    SalesWorkflowState,
)

__all__ = ["SalesWorkflowState"]