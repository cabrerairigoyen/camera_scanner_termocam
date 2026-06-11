import json
import logging
import os
import tempfile
from pathlib import Path


LOGGER = logging.getLogger(__name__)
DEFAULT_STATE_PATH = "pi/data/document_state.json"


def _state_path() -> Path:
    return Path(os.getenv("TERMOCAM_PI_STATE_PATH", DEFAULT_STATE_PATH))


def load_state() -> dict:
    path = _state_path()
    try:
        with path.open("r", encoding="utf-8") as handle:
            state = json.load(handle)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError, TypeError) as exc:
        LOGGER.warning("Unable to load Pi document state from %s: %s", path, exc)
        return {}
    if not isinstance(state, dict):
        LOGGER.warning("Ignoring non-object Pi document state in %s", path)
        return {}
    return state


def save_state(state: dict) -> None:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_path = Path(handle.name)
            json.dump(state, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass


def clear_state() -> None:
    path = _state_path()
    try:
        path.unlink()
    except FileNotFoundError:
        return
    except OSError as exc:
        LOGGER.warning("Unable to clear Pi document state at %s: %s", path, exc)
