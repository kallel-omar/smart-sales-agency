"""
Compatibility import for the previous Sales workflow location.

New code should use:
app.departments.sales.workflows.NewLeadWorkflow
"""

from app.departments.sales.workflows.new_lead import (
    NewLeadWorkflow,
)

SalesWorkflow = NewLeadWorkflow

__all__ = [
    "NewLeadWorkflow",
    "SalesWorkflow",
]