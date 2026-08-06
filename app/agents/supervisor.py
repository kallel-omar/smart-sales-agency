"""
Compatibility imports for the previous Sales supervisor location.

New code should import from:
app.departments.sales.supervisor
"""

from app.departments.sales.supervisor import (
    SalesDepartmentSupervisor,
    SalesEvent,
)

SupervisorAgent = SalesDepartmentSupervisor

__all__ = [
    "SalesEvent",
    "SupervisorAgent",
]