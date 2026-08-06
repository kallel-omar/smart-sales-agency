from app.agents.supervisor import SalesEvent, SupervisorAgent


def test_supervisor_routes_new_lead():
    assert SupervisorAgent().route(SalesEvent.NEW_LEAD) == "research_and_qualify"
