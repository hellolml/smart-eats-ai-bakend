from app.main import _should_warmup_rag


def test_rag_warmup_is_skipped_in_test_env():
    assert _should_warmup_rag("test") is False


def test_rag_warmup_runs_outside_test_env():
    assert _should_warmup_rag("development") is True
