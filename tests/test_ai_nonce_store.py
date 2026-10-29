from concurrent.futures import ThreadPoolExecutor

from app.ai.nonce_store import SQLiteNonceStore


def test_nonce_is_single_use_and_persists_across_instances(tmp_path):
    path = tmp_path / "security" / "nonces.sqlite3"
    first = SQLiteNonceStore(path)
    second = SQLiteNonceStore(path)

    assert first.consume_nonce("nonce-unique-123456")
    assert not second.consume_nonce("nonce-unique-123456")
    assert second.consume_nonce("nonce-another-123456")


def test_concurrent_consumers_accept_nonce_exactly_once(tmp_path):
    store_path = tmp_path / "nonces.sqlite3"
    nonce = "concurrent-nonce-123456"

    def consume(_):
        return SQLiteNonceStore(store_path).consume_nonce(nonce)

    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(consume, range(16)))

    assert outcomes.count(True) == 1
    assert outcomes.count(False) == 15


def test_rejects_invalid_nonce_and_too_short_retention(tmp_path):
    import pytest

    with pytest.raises(ValueError, match="at least 930"):
        SQLiteNonceStore(tmp_path / "bad.sqlite3", retention_seconds=10)

    store = SQLiteNonceStore(tmp_path / "nonces.sqlite3")
    assert not store.consume_nonce("")
    assert not store.consume_nonce("x" * 129)
