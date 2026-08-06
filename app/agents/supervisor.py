from enum import StrEnum


class SalesEvent(StrEnum):
    NEW_LEAD = "new_lead"
    INBOUND_MESSAGE = "inbound_message"
    FOLLOW_UP_DUE = "follow_up_due"


class SupervisorAgent:
    """Deterministic router for the MVP.

    Do not use an LLM for routing until the workflow is observable and tested.
    """

    def route(self, event: SalesEvent) -> str:
        routes = {
            SalesEvent.NEW_LEAD: "research_and_qualify",
            SalesEvent.INBOUND_MESSAGE: "sales_conversation",
            SalesEvent.FOLLOW_UP_DUE: "follow_up",
        }
        return routes[event]
