import json

import pytest

from pi import document_state_store


def test_save_and_load_state(monkeypatch, tmp_path):
    path = tmp_path / "nested" / "document_state.json"
    monkeypatch.setenv("TERMOCAM_PI_STATE_PATH", str(path))
    state = {"document_id": "doc_1", "next_page_number": 3, "pages": []}

    document_state_store.save_state(state)

    assert path.exists()
    assert document_state_store.load_state() == state


def test_load_missing_state_returns_empty_dict(monkeypatch, tmp_path):
    monkeypatch.setenv("TERMOCAM_PI_STATE_PATH", str(tmp_path / "missing.json"))
    assert document_state_store.load_state() == {}


def test_load_corrupt_state_returns_empty_dict(monkeypatch, tmp_path):
    path = tmp_path / "corrupt.json"
    path.write_text("{not-json", encoding="utf-8")
    monkeypatch.setenv("TERMOCAM_PI_STATE_PATH", str(path))
    assert document_state_store.load_state() == {}


def test_failed_save_does_not_replace_existing_state(monkeypatch, tmp_path):
    path = tmp_path / "document_state.json"
    path.write_text(json.dumps({"document_id": "doc_old"}), encoding="utf-8")
    monkeypatch.setenv("TERMOCAM_PI_STATE_PATH", str(path))

    with pytest.raises(TypeError):
        document_state_store.save_state({"invalid": object()})

    assert json.loads(path.read_text(encoding="utf-8")) == {"document_id": "doc_old"}


def test_clear_state_is_idempotent(monkeypatch, tmp_path):
    path = tmp_path / "document_state.json"
    monkeypatch.setenv("TERMOCAM_PI_STATE_PATH", str(path))
    document_state_store.save_state({"document_id": "doc_1"})

    document_state_store.clear_state()
    document_state_store.clear_state()

    assert not path.exists()
