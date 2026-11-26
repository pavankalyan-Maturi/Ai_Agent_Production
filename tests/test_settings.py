import pytest

from apps.api.settings import Settings


def test_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PAP_ENV", "staging")
    monkeypatch.setenv("PAP_LLM_API_KEY", "sk-test-123")
    s = Settings()
    assert s.env == "staging"
    assert "sk-test-123" not in repr(s)  # SecretStr masks it


def test_pii_capture_off_by_default() -> None:
    assert Settings().otel_capture_content is False


def test_rejects_unknown_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PAP_ENV", "dev-typo")
    with pytest.raises(ValueError):
        Settings()
