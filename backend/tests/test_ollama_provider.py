"""OllamaProvider against a local fake Ollama server (no model, no network beyond 127.0.0.1)."""

import asyncio
from datetime import UTC, datetime

import pytest

from app.ai.provider import AIOperationError, AIUnavailableError
from app.ai.providers import build_ai_provider
from app.ai.providers.ollama import BRIEFING_SYSTEM_PROMPT, OllamaProvider
from app.ai.schemas import BriefingRequest, DealContext, EvidenceItem
from app.ai.service import AIService
from app.config import Settings
from tests.fake_ollama import FakeOllama

REQUEST = BriefingRequest(
    deal=DealContext(customer_id="cus_a", deal_id="deal_a", customer_name="Aurora Logistics",
                     deal_title="Pune pilot", stage="proposal", status="open"),
    evidence=[
        EvidenceItem(ref="R1", kind="recorded", text="Deal 'Pune pilot': value USD 42,000.", source_type="deal",
                     source_id="deal_a"),
        EvidenceItem(ref="N1", kind="rep_note", text="call (rep note): SOC 2 report is mandatory.",
                     source_type="interaction", source_id="int_1", occurred_at=datetime(2026, 9, 20, tzinfo=UTC)),
    ],
    as_of=datetime(2026, 9, 28, 12, tzinfo=UTC),
)


@pytest.fixture
def fake():
    server = FakeOllama().start()
    yield server
    server.stop()


def provider(fake, **kw) -> OllamaProvider:
    return OllamaProvider(fake.url, kw.pop("model", "qwen3:4b"), **{"timeout_seconds": 5, **kw})


def run(coro):
    return asyncio.run(coro)


def test_factory_builds_ollama_from_settings():
    s = Settings(_env_file=None, ai_provider="ollama")
    p = build_ai_provider(s)
    assert isinstance(p, OllamaProvider)
    assert (p.model, p.num_ctx, p.think, p.base_url) == ("qwen3:4b", 4096, False, "http://127.0.0.1:11434")


def test_status_available_when_model_is_pulled(fake):
    status = run(provider(fake).status())
    assert status.available is True and status.implemented is True and status.model == "qwen3:4b"


def test_status_reports_missing_model(fake):
    fake.models = ["llama3.2:3b"]
    status = run(provider(fake).status())
    assert status.available is False and "not pulled" in status.reason and "ollama pull qwen3:4b" in status.reason


def test_status_reports_unreachable_server():
    status = run(OllamaProvider("http://127.0.0.1:9", "qwen3:4b").status())
    assert status.available is False and "not reachable" in status.reason


def test_briefing_request_is_deterministic_and_local(fake):
    fake.set_briefing([{"text": "The deal value is USD 42,000.", "kind": "recorded", "citations": ["R1"]}],
                      ["Decision date"])
    result = run(provider(fake).generate_briefing(REQUEST))
    sent = fake.requests[0]
    assert sent["model"] == "qwen3:4b" and sent["stream"] is False and sent["think"] is False
    assert sent["options"] == {"temperature": 0, "num_ctx": 4096}
    assert sent["format"]["required"] == ["claims", "missing_evidence"]
    assert sent["messages"][0]["content"] == BRIEFING_SYSTEM_PROMPT
    user = sent["messages"][1]["content"]
    assert "[R1] (recorded) Deal 'Pune pilot'" in user and "[N1] (rep_note 2026-09-20)" in user
    assert [c.text for c in result.claims] == ["The deal value is USD 42,000."]
    assert result.missing_evidence == ["Decision date"]
    assert result.generated.provider == "ollama" and result.generated.model == "qwen3:4b"


def test_unknown_claim_kinds_become_inference(fake):
    fake.set_briefing([{"text": "x", "kind": "statement", "citations": []}])
    assert run(provider(fake).generate_briefing(REQUEST)).claims[0].kind == "inference"


@pytest.mark.parametrize("content", ["not json at all", '{"claims": "nope"}', '{"missing_evidence": []}'])
def test_malformed_output_is_an_operation_error(fake, content):
    fake.chat_content = content
    with pytest.raises(AIOperationError, match="does not match the briefing schema"):
        run(provider(fake).generate_briefing(REQUEST))


def test_truncated_output_is_rejected(fake):
    fake.done_reason = "length"
    with pytest.raises(AIOperationError, match="truncated"):
        run(provider(fake).generate_briefing(REQUEST))


def test_missing_model_is_unavailable_not_a_fallback(fake):
    fake.models = ["llama3.2:3b"]
    with pytest.raises(AIUnavailableError, match="not available locally"):
        run(provider(fake).generate_briefing(REQUEST))
    assert fake.requests[0]["model"] == "qwen3:4b"  # asked for the configured model only


def test_server_error_is_an_operation_error(fake):
    fake.chat_status = 500
    with pytest.raises(AIOperationError, match="HTTP 500"):
        run(provider(fake).generate_briefing(REQUEST))


def test_timeout_is_unavailable(fake):
    fake.delay_seconds = 2
    with pytest.raises(AIUnavailableError, match="did not answer within"):
        run(provider(fake, timeout_seconds=0.5).generate_briefing(REQUEST))


def test_unreachable_server_is_unavailable():
    with pytest.raises(AIUnavailableError, match="not reachable"):
        run(OllamaProvider("http://127.0.0.1:9", "qwen3:4b").generate_briefing(REQUEST))


def test_other_operations_are_explicitly_unavailable(fake):
    p = provider(fake)
    for call in (p.analyze_objections, p.draft_follow_up):  # compare_strategies: TM4, see test_ollama_strategy.py
        with pytest.raises(AIUnavailableError, match="not implemented"):
            run(call(None))


def test_through_ai_service_errors_are_not_retried(fake):
    fake.chat_content = "garbage"
    service = AIService(provider(fake), timeout_seconds=5, max_retries=2, retry_base_delay=0)
    with pytest.raises(AIOperationError):
        run(service.generate_briefing(REQUEST))
    assert len(fake.requests) == 1
