from enum import StrEnum


class SalesEvent(StrEnum):
    """Sales Department events understood by the deterministic router."""

    NEW_LEAD = "new_lead"
    INBOUND_MESSAGE = "inbound_message"
    FOLLOW_UP_DUE = "follow_up_due"


class SalesDepartmentSupervisor:
    """
    Deterministic Sales Department router.

    The supervisor decides which Sales workflow should own a task.

    We intentionally avoid LLM-based routing here because known routing rules
    should remain deterministic, cheap, observable, and testable.
    """

    def route(self, event: SalesEvent) -> str:
        routes = {
            SalesEvent.NEW_LEAD: "research_and_qualify",
            SalesEvent.INBOUND_MESSAGE: "sales_conversation",
            SalesEvent.FOLLOW_UP_DUE: "follow_up",
        }

        return routes[event]