from types import SimpleNamespace

import pytest

from curation import brief, sample_data
from curation.pipeline import queries_for, run


def _msg(stop, text="## Diagnosi\nok"):
    return SimpleNamespace(
        stop_reason=stop, model="claude-opus-5-5",
        content=[SimpleNamespace(type="text", text=text)],
        usage=SimpleNamespace(input_tokens=100, output_tokens=50),
    )


class FakeStream:
    def __init__(self, msg): self.msg = msg
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def get_final_message(self): return self.msg


class FakeClient:
    def __init__(self, responses):
        self.calls, self._responses = [], list(responses)
        self.beta = SimpleNamespace(messages=SimpleNamespace(stream=self._stream))

    def _stream(self, **kw):
        self.calls.append(kw)
        return FakeStream(self._responses.pop(0))


@pytest.fixture
def row_and_queries():
    ds = sample_data.build()
    df = run(ds)
    row = df.iloc[0]
    return row, queries_for(ds, row["url"])


def test_prompt_contains_data(row_and_queries):
    row, q = row_and_queries
    p = brief.build_user_prompt(row, q, use_web=True)
    assert row["url"] in p and q.iloc[0]["query"] in p
    assert "<articolo_attuale>" in p and "ricerca web" in p


def test_pause_turn_is_resumed(monkeypatch, row_and_queries):
    fake = FakeClient([_msg("pause_turn", ""), _msg("end_turn")])
    monkeypatch.setattr(brief.anthropic, "Anthropic", lambda **kw: fake)
    row, q = row_and_queries
    res = brief.generate_brief(row, q, use_web=True)
    assert len(fake.calls) == 2
    assert fake.calls[1]["messages"][-1]["role"] == "assistant"
    assert fake.calls[0]["tools"][0]["type"] == "web_search_20260209"
    assert fake.calls[0]["fallbacks"] == "default"
    assert res.markdown.startswith("## Diagnosi") and res.input_tokens == 200


def test_no_tools_without_web(monkeypatch, row_and_queries):
    fake = FakeClient([_msg("end_turn")])
    monkeypatch.setattr(brief.anthropic, "Anthropic", lambda **kw: fake)
    brief.generate_brief(*row_and_queries, use_web=False)
    assert "tools" not in fake.calls[0]


def test_refusal_raises(monkeypatch, row_and_queries):
    fake = FakeClient([_msg("refusal", "")])
    monkeypatch.setattr(brief.anthropic, "Anthropic", lambda **kw: fake)
    with pytest.raises(RuntimeError):
        brief.generate_brief(*row_and_queries)


def test_chat_prompt_is_self_contained(row_and_queries):
    p = brief.build_chat_prompt(*row_and_queries)
    assert p.startswith("Sei un senior SEO content strategist")
    assert "## Checklist per il redattore" in p and row_and_queries[0]["url"] in p
