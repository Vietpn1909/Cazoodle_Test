from datetime import date

from app.core.config import Settings


def test_window_start_is_two_years_before_snapshot() -> None:
    s = Settings(snapshot_date=date(2026, 11, 16), window_years=2)
    assert s.window_start == date(2024, 11, 16)


def test_window_start_handles_leap_day() -> None:
    s = Settings(snapshot_date=date(2028, 2, 29), window_years=2)
    assert s.window_start == date(2026, 2, 28)


def test_cors_origins_parsed_from_csv() -> None:
    s = Settings(api_cors_origins="http://a.test, http://b.test ,")
    assert s.cors_origins == ["http://a.test", "http://b.test"]


def test_secrets_are_masked() -> None:
    s = Settings(llm_api_key="sk-secret-value")  # type: ignore[arg-type]
    assert "sk-secret-value" not in repr(s)
