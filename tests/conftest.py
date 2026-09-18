import pytest

from app.schemas import LLMInterpretation


class FakeLLM:
    """Stand-in for the OpenRouter chat model. `responses` is a list of LLMInterpretation | Exception,
    consumed one per call (last one repeats). Used to drive build_graph(llm=...) offline."""

    def __init__(self, responses: list[LLMInterpretation | Exception]):
        self.responses = responses
        self.calls: list[list] = []

    async def ainvoke(self, messages):
        self.calls.append(messages)
        idx = min(len(self.calls) - 1, len(self.responses) - 1)
        result = self.responses[idx]
        if isinstance(result, Exception):
            raise result
        return result


@pytest.fixture
def fake_llm():
    return FakeLLM
