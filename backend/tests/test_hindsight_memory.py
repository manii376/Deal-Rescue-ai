"""Offline tests for the Hindsight facade: validation, error mapping, redaction."""

import pytest

from app.services.hindsight_memory import (
    HindsightMemory,
    MemoryUnavailableError,
    _record,
    _redact,
    customer_tag,
)
from tests.conftest import UNREACHABLE_URL

FAKE_KEY = "sk-ant-test-not-a-real-key-789"


@pytest.fixture
async def memory():
    mem = HindsightMemory(UNREACHABLE_URL, "test-bank", api_key=FAKE_KEY, timeout_seconds=5)
    yield mem
    await mem.aclose()


async def test_health_reports_unreachable_instead_of_raising(memory):
    status = await memory.health()
    assert status.reachable is False
    assert status.healthy is False


async def test_recall_unreachable_raises_safe_error(memory):
    with pytest.raises(MemoryUnavailableError) as info:
        await memory.recall("anything", tags=[customer_tag("a")])
    assert FAKE_KEY not in str(info.value)
    assert UNREACHABLE_URL in str(info.value)


async def test_retain_rejects_empty_content(memory):
    with pytest.raises(ValueError):
        await memory.retain("   ", document_id="d1", tags=["customer:a"])


async def test_retain_requires_tags(memory):
    with pytest.raises(ValueError):
        await memory.retain("text", document_id="d1", tags=[])


def test_redact_removes_known_secret_and_key_patterns():
    text = f"key={FAKE_KEY} other=sk-ant-abc123 header=Bearer abc.def"
    redacted = _redact(text, [FAKE_KEY])
    assert FAKE_KEY not in redacted
    assert "sk-ant-abc123" not in redacted
    assert "abc.def" not in redacted


def test_record_normalises_dicts_and_objects():
    from types import SimpleNamespace

    as_dict = _record({"id": 1, "text": "t", "fact_type": "world", "tags": ["x"]})
    assert as_dict.id == "1" and as_dict.type == "world" and as_dict.tags == ["x"]
    as_obj = _record(SimpleNamespace(id="m1", text="t", type="experience", tags=None, metadata=None))
    assert as_obj.type == "experience" and as_obj.tags == [] and as_obj.metadata == {}


async def test_get_memory_unreachable_raises_safe_error(memory):
    with pytest.raises(MemoryUnavailableError) as info:
        await memory.get_memory("some-memory-id")
    assert FAKE_KEY not in str(info.value)
