import json

from pi.document_state_store import load_state, save_state


def test_document_start(live_server):
    client = live_server.app.test_client()
    response = client.post("/document/start", json={"course": "GFN252", "language": "fr", "title": "Exam"})
    assert response.status_code == 200
    data = response.get_json()
    assert data["document_id"] == "doc_test"
    assert data["status"] == "DOCUMENT_OPEN"
    assert data["next_page_number"] == 1
    persisted = load_state()
    assert persisted["document_id"] == "doc_test"
    assert persisted["next_page_number"] == 1


def test_capture_still_increments_page_only_on_success(live_server, tmp_path):
    client = live_server.app.test_client()
    client.post("/document/start", json={})

    response = client.post("/document/capture-still")
    assert response.status_code == 200
    data = response.get_json()
    assert data["page_number"] == 1
    assert live_server._document_state_snapshot()["next_page_number"] == 2
    persisted = load_state()
    assert persisted["next_page_number"] == 2
    assert persisted["pages"][0]["capture_mode"] == "still"

    live_server.document_client.upload_response = {"ok": False, "error": {"code": "SERVER_OFFLINE", "message": "offline"}}
    response = client.post("/document/capture-still")
    assert response.status_code == 503
    assert live_server._document_state_snapshot()["next_page_number"] == 2
    assert load_state()["next_page_number"] == 2


def test_finish_document(live_server):
    client = live_server.app.test_client()
    client.post("/document/start", json={})
    response = client.post("/document/finish", json={"expected_page_count": 1, "solve": True, "answer_mode": "standard"})
    assert response.status_code == 200
    data = response.get_json()
    assert data["status"] == "DOCUMENT_PROCESSING"
    assert data["job_id"] == "job_finish"


def test_document_events(live_server):
    live_server.document_client.events_response = {
        "ok": True,
        "events": [
            {
                "sequence": 1,
                "event_type": "AUDIO_FEEDBACK",
                "severity": "success",
                "message_key": "page_accepted",
                "spoken_text": "Page 1 accepted.",
                "page_number": 1,
            }
        ],
        "next_after_sequence": 1,
    }
    client = live_server.app.test_client()
    client.post("/document/start", json={})
    response = client.get("/document/events")
    assert response.status_code == 200
    data = response.get_json()
    assert data["next_after_sequence"] == 1
    assert data["events"][0]["spoken_text"] == "Page 1 accepted."
    assert live_server._document_state_snapshot()["last_event_sequence"] == 1
    assert load_state()["last_event_sequence"] == 1


def test_startup_recovery_with_server_reachable(live_server):
    save_state({
        "document_id": "doc_test",
        "status": "WAITING_QUALITY",
        "next_page_number": 2,
        "last_event_sequence": 8,
        "pages": [{
            "page_id": "page_1",
            "page_number": 1,
            "capture_mode": "still",
            "status": "ACCEPTED",
        }],
    })
    live_server.document_client.document_response = {
        "ok": True,
        "document_id": "doc_test",
        "status": "CAPTURING",
        "next_page_number": 3,
        "pages": [
            {"page_id": "page_1", "page_number": 1, "status": "ACCEPTED"},
            {"page_id": "page_2", "page_number": 2, "status": "ACCEPTED"},
        ],
    }
    live_server._reset_document_state()

    recovered = live_server.recover_document_state()

    assert recovered["document_id"] == "doc_test"
    assert recovered["status"] == "DOCUMENT_OPEN"
    assert recovered["next_page_number"] == 3
    assert recovered["last_event_sequence"] == 8
    assert recovered["pages"][0]["capture_mode"] == "still"
    assert recovered["pages"][1]["capture_mode"] == "unknown"


def test_startup_recovery_with_server_offline(live_server):
    save_state({
        "document_id": "doc_test",
        "status": "DOCUMENT_OPEN",
        "next_page_number": 3,
        "last_event_sequence": 8,
        "pages": [{"page_id": "page_1", "page_number": 1, "capture_mode": "still", "status": "ACCEPTED"}],
    })
    live_server.document_client.document_response = {
        "ok": False,
        "error": {"code": "SERVER_OFFLINE", "message": "offline"},
    }
    live_server._reset_document_state()

    recovered = live_server.recover_document_state()

    assert recovered["document_id"] == "doc_test"
    assert recovered["status"] == "SERVER_OFFLINE"
    assert recovered["next_page_number"] == 3
    assert recovered["last_event_sequence"] == 8
    status = live_server.app.test_client().get("/document/status").get_json()
    assert status["server_reachable"] is False
    assert status["needs_resync"] is True
    assert status["last_error"]["code"] == "SERVER_OFFLINE"


def test_startup_recovery_tolerates_invalid_counter_values(live_server):
    save_state({
        "document_id": None,
        "status": "IDLE",
        "next_page_number": "not-a-number",
        "last_event_sequence": {"invalid": True},
        "pages": "not-a-list",
    })
    live_server._reset_document_state()

    recovered = live_server.recover_document_state()

    assert recovered["next_page_number"] == 1
    assert recovered["last_event_sequence"] == 0
    assert recovered["pages"] == []


def test_document_attach(live_server):
    live_server.document_client.document_response = {
        "ok": True,
        "document_id": "doc_attached",
        "status": "CAPTURING",
        "next_page_number": 4,
        "pages": [
            {"page_id": "page_1", "page_number": 1, "status": "ACCEPTED"},
            {"page_id": "page_2", "page_number": 2, "status": "ACCEPTED"},
            {"page_id": "page_3", "page_number": 3, "status": "OCR_DONE"},
        ],
    }

    response = live_server.app.test_client().post("/document/attach", json={"document_id": "doc_attached"})

    assert response.status_code == 200
    data = response.get_json()
    assert data["document_id"] == "doc_attached"
    assert data["next_page_number"] == 4
    assert len(data["pages"]) == 3
    assert load_state()["document_id"] == "doc_attached"


def test_document_resync_preserves_event_sequence(live_server):
    live_server._set_document_state(
        document_id="doc_test",
        status="DOCUMENT_OPEN",
        next_page_number=2,
        last_event_sequence=12,
        pages=[],
    )
    live_server._persist_document_state()
    live_server.document_client.document_response = {
        "ok": True,
        "document_id": "doc_test",
        "status": "PROCESSING",
        "next_page_number": 3,
        "pages": [{"page_id": "page_2", "page_number": 2, "status": "ACCEPTED"}],
    }

    response = live_server.app.test_client().post("/document/resync")

    assert response.status_code == 200
    data = response.get_json()
    assert data["status"] == "DOCUMENT_PROCESSING"
    assert data["next_page_number"] == 3
    assert data["last_event_sequence"] == 12
    assert load_state()["last_event_sequence"] == 12


def test_reset_clears_state(live_server):
    client = live_server.app.test_client()
    client.post("/document/start", json={})
    response = client.post("/document/reset")
    assert response.status_code == 200
    data = response.get_json()
    assert data["status"] == "IDLE"
    assert live_server._document_state_snapshot()["document_id"] is None
    assert load_state() == {}


def test_legacy_routes_still_exist(live_server):
    rules = {rule.rule for rule in live_server.app.url_map.iter_rules()}
    assert "/process-highres" in rules
    assert "/sweep/start" in rules
    assert "/sweep/stop" in rules
    assert "/photo" in rules
    assert "/document/attach" in rules
    assert "/document/resync" in rules
