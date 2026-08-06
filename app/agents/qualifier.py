"""
Compatibility imports for the previous Sales agent package.

New code should import from:
app.departments.sales.agents.qualifier
"""

from app.departments.sales.agents.qualifier import (
    QualificationAgent,
    QualificationResult,
)

__all__ = [
    "QualificationAgent",
    "QualificationResult",
]