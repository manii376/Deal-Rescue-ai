import pytest
from pydantic import ValidationError

from app.config import Settings

FAKE_KEY = "sk-ant-test-not-a-real-key-123"


def make(**overrides) -> Settings:
    return Settings(_env_file=None, **overrides)


def test_defaults_without_credentials():
    settings = make()
    assert settings.hindsight_base_url == "http://127.0.0.1:8888"
    assert settings.hindsight_bank_id == "deal-rescue-demo"
    assert settings.describe()["anthropic_api_key"] == "missing"


def test_blank_keys_are_treated_as_missing():
    settings = make(anthropic_api_key="  ", hindsight_api_key="")
    assert settings.anthropic_api_key is None
    assert settings.hindsight_api_key is None


def test_secret_never_appears_in_repr_or_describe():
    settings = make(anthropic_api_key=FAKE_KEY)
    assert FAKE_KEY not in repr(settings)
    assert FAKE_KEY not in str(settings.describe())
    assert settings.describe()["anthropic_api_key"] == "set"


def test_invalid_base_url_rejected():
    with pytest.raises(ValidationError):
        make(hindsight_base_url="localhost:8888")


@pytest.mark.parametrize("bank_id", ["", "has space", "slash/bank", "x" * 65])
def test_invalid_bank_id_rejected(bank_id):
    with pytest.raises(ValidationError):
        make(hindsight_bank_id=bank_id)


def test_trailing_slash_is_stripped():
    assert make(hindsight_base_url="http://127.0.0.1:8888/").hindsight_base_url == "http://127.0.0.1:8888"
