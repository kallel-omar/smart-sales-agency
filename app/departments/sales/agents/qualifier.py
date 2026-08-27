from dataclasses import dataclass

from app.departments.sales.agents.base import AgentContext
from app.models import Lead


@dataclass(slots=True)
class QualificationResult:
    score: int
    qualified: bool
    reasons: list[str]


class QualificationAgent:
    def __init__(self, context: AgentContext, threshold: int = 55):
        self.context = context
        self.threshold = threshold

    async def run(
        self,
        lead: Lead,
        research: dict,
    ) -> QualificationResult:
        result = self.evaluate(lead, research)
        self.context.repository.update_lead_score(
            lead,
            result.score,
            result.qualified,
        )
        return result

    def evaluate(
        self,
        lead: Lead,
        research: dict,
    ) -> QualificationResult:
        """Calculate the legacy result without claiming persistence authority."""

        score = 10
        reasons: list[str] = []

        if lead.email or lead.phone:
            score += 20
            reasons.append("A direct contact channel is available")

        if lead.website:
            score += 15
            reasons.append("A company website is available")

        if lead.job_title:
            score += 15
            reasons.append("The lead role is known")

        if lead.notes and len(lead.notes.strip()) >= 20:
            score += 15
            reasons.append("Useful discovery notes are available")

        if research.get("opportunities"):
            score += min(
                20,
                len(research["opportunities"]) * 10,
            )
            reasons.append(
                "The research brief identified relevant opportunities"
            )

        score = min(score, 100)
        qualified = score >= self.threshold
        return QualificationResult(
            score=score,
            qualified=qualified,
            reasons=reasons,
        )
