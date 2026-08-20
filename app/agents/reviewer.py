"""审查 agent（reviewer）。首版 stub。"""

from app.agents.base import StubAgent


class Reviewer(StubAgent):
    role = "reviewer"


reviewer = Reviewer()
