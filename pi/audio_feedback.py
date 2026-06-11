import logging
from typing import Iterable


LOGGER = logging.getLogger(__name__)

MESSAGE_FALLBACKS = {
    "page_accepted": "Page accepted.",
    "page_blurry": "Page is blurry.",
    "page_cropped": "Page is cropped.",
    "low_ocr_confidence": "OCR confidence is low.",
    "processing_started": "Processing started.",
    "processing_finished": "Processing finished.",
    "answers_ready": "Answers are ready.",
    "job_failed": "Job failed.",
    "retake_page": "Retake page.",
    "cloud_offline": "Cloud is offline.",
    "camera_disconnected": "Camera disconnected.",
    "resync_complete": "Resync complete.",
    "solver_unavailable": "Solver unavailable.",
    "tts_failed": "Audio feedback failed.",
}


def handle_audio_events(events: list[dict] | Iterable[dict]) -> None:
    for event in events or []:
        if not isinstance(event, dict):
            continue
        message_key = event.get("message_key")
        spoken_text = event.get("spoken_text") or MESSAGE_FALLBACKS.get(message_key) or str(message_key or "")
        severity = event.get("severity", "info")
        if spoken_text:
            LOGGER.info("audio_feedback severity=%s key=%s text=%s", severity, message_key, spoken_text)
            print(spoken_text)
