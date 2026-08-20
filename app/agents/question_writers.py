"""出题 agent（choice / blank / short_answer）。首版 stub。"""

from app.agents.base import StubAgent


class ChoiceWriter(StubAgent):
    role = "choice_writer"


class BlankWriter(StubAgent):
    role = "blank_writer"


class ShortWriter(StubAgent):
    role = "short_writer"


choice_writer = ChoiceWriter()
blank_writer = BlankWriter()
short_writer = ShortWriter()
