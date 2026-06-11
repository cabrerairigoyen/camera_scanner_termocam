"""
solver_bridge.py
================
Bridge between camera_scanner_termocam OCR results and the math_solver_backend.

After a sweep job completes OCR, this module:
  1. Extracts the text from the ocr.json result
  2. Writes it to a temp file inside the job directory
  3. Launches process_document_to_brain.py as a background subprocess
     using --skip-ocr and --ocr-text flags
  4. Returns immediately (non-blocking) — the solver runs in a daemon thread

Environment variables:
  AUTO_SOLVE_AFTER_OCR      (bool, default "false")  Master on/off switch
  MIN_OCR_CONFIDENCE        (float, default "0.7")   Minimum mean confidence to trigger
  SOLVER_SEND_TO_DISPLAY    (bool, default "true")   Whether solver POSTs to HDMI display
"""

import os
import sys
import json
import threading
import subprocess
import traceback
from pathlib import Path
from typing import Optional

# ── Resolve paths ────────────────────────────────────────────────────────────
# camera_scanner_termocam/server/solver_bridge.py  →  repo root
_THIS_FILE = Path(__file__).resolve()
_REPO_ROOT = _THIS_FILE.parent.parent.parent          # Actuarial AI Brain/
_SOLVER_SCRIPT = _REPO_ROOT / "math_solver_backend" / "process_document_to_brain.py"
_CO_SCIENTIST_ADAPTER = _REPO_ROOT / "math_solver_backend" / "BRAIN" / "co_scientist_adapter.py"

# Dummy image placeholder – process_document_to_brain.py requires a positional
# image_path arg even with --skip-ocr.  We pass a blank sentinel value and rely
# on the patched existence-check that skips validation when --skip-ocr is set.
_DUMMY_IMAGE = str(_REPO_ROOT / "math_solver_backend" / "data" / "ai_results_payload.json")


def _extract_text_from_ocr_result(ocr_result: dict) -> Optional[str]:
    """Pull the best available text string from an ocr.json dict."""
    # Try common top-level keys first
    for key in ("text", "latex", "markdown", "full_text"):
        val = ocr_result.get(key)
        if val and val.strip():
            return val.strip()

    # Reconstruct from line objects
    lines = ocr_result.get("lines", [])
    if lines:
        assembled = "\n".join(
            line.get("text", "") for line in lines if line.get("text", "").strip()
        )
        if assembled.strip():
            return assembled.strip()

    return None


def _run_solver(ocr_text: str, job_path: str, send_to_display: bool) -> dict:
    """
    Blocking call to process_document_to_brain.py with the OCR text.
    Intended to be called from a background thread.
    """
    status = {"launched": False, "returncode": None, "error": None}

    if not _SOLVER_SCRIPT.exists():
        status["error"] = f"Solver script not found: {_SOLVER_SCRIPT}"
        return status

    # Write OCR text to a temp file inside the job directory
    ocr_txt_path = Path(job_path) / "ocr_text_for_solver.txt"
    try:
        ocr_txt_path.write_text(ocr_text, encoding="utf-8")
    except Exception as e:
        status["error"] = f"Cannot write ocr_text_for_solver.txt: {e}"
        return status

    cmd = [
        sys.executable,
        str(_SOLVER_SCRIPT),
        _DUMMY_IMAGE,
        "--skip-ocr",
        "--ocr-text", str(ocr_txt_path),
    ]

    if not send_to_display:
        cmd.append("--skip-display")

    env = os.environ.copy()
    env["PYTHONPATH"] = str(_REPO_ROOT)

    status["launched"] = True
    print(f"[solver_bridge] Launching legacy solver: {' '.join(cmd[:4])} ...")

    try:
        result = subprocess.run(
            cmd,
            cwd=str(_REPO_ROOT),
            env=env,
            capture_output=True,
            text=True,
            timeout=int(os.getenv("SOLVER_TIMEOUT_SECONDS", "1800")),
        )
        status["returncode"] = result.returncode
        if result.returncode == 0:
            print("[solver_bridge] ✅ Legacy solver completed successfully.")
        else:
            print(f"[solver_bridge] ❌ Legacy solver exited with code {result.returncode}")
            status["error"] = result.stderr[-1000:] if result.stderr else "unknown"
            print(f"[solver_bridge] STDERR: {status['error']}")
    except subprocess.TimeoutExpired:
        status["error"] = "Solver timed out"
        print("[solver_bridge] ❌ Legacy solver timed out.")
    except Exception as exc:
        status["error"] = str(exc)
        traceback.print_exc()

    return status


def _run_co_scientist(ocr_text: str, job_path: str, send_to_display: bool) -> dict:
    """
    Blocking call to the co_scientist_adapter.run_co_scientist().
    Intended to be called from a background thread.
    """
    status = {"launched": False, "returncode": None, "error": None, "engine": "co_scientist"}

    if not _CO_SCIENTIST_ADAPTER.exists():
        status["error"] = f"co_scientist_adapter not found: {_CO_SCIENTIST_ADAPTER}"
        return status

    # Add BRAIN dir to path so the adapter can find display_client etc.
    brain_dir = str(_REPO_ROOT / "math_solver_backend" / "BRAIN")
    solver_dir = str(_REPO_ROOT / "math_solver_backend")
    co_dir = str(_REPO_ROOT / "open-ai-co-scientist")
    for p in [brain_dir, solver_dir, co_dir, str(_REPO_ROOT)]:
        if p not in sys.path:
            sys.path.insert(0, p)

    try:
        from math_solver_backend.BRAIN.co_scientist_adapter import run_co_scientist  # type: ignore
        status["launched"] = True
        print("[solver_bridge] 🧠 Launching co-scientist adapter...")
        result = run_co_scientist(
            ocr_text=ocr_text,
            send_to_display=send_to_display,
        )
        if result:
            print("[solver_bridge] ✅ Co-scientist adapter completed.")
            status["returncode"] = 0
        else:
            print("[solver_bridge] ❌ Co-scientist adapter returned None.")
            status["error"] = "adapter returned None"
            status["returncode"] = 1
    except Exception as exc:
        status["error"] = str(exc)
        print(f"[solver_bridge] ❌ Co-scientist error: {exc}")
        traceback.print_exc()

    return status


def forward_ocr_to_solver(
    ocr_result: dict,
    job_path: str,
    blocking: bool = False,
) -> dict:
    """
    Forward OCR output from a sweep job to the math_solver_backend.

    Args:
        ocr_result:  The dict returned by server.ocr.run_ocr() (ocr.json content).
        job_path:    Absolute path to the job output directory (for temp files).
        blocking:    If True, wait for solver to finish before returning.
                     If False (default), run in background thread.

    Returns:
        dict with keys:
          - "skipped"   (bool)   True if bridge was bypassed
          - "reason"    (str)    Why skipped (if applicable)
          - "launched"  (bool)   Whether subprocess was started
          - "thread"    (str)    "background" | "blocking"
          - "returncode" (int|None)  Only set when blocking=True
    """
    # ── Read config ─────────────────────────────────────────────────────────
    use_co_scientist = os.getenv("USE_CO_SCIENTIST", "false").lower() == "true"
    send_to_display = os.getenv("SOLVER_SEND_TO_DISPLAY", "true").lower() == "true"
    min_confidence = float(os.getenv("MIN_OCR_CONFIDENCE", "0.7"))

    # ── Guard: no text ───────────────────────────────────────────────────────
    text = _extract_text_from_ocr_result(ocr_result)
    if not text:
        print("[solver_bridge] ⚠️  No usable OCR text found — skipping solver.")
        return {"skipped": True, "reason": "empty_ocr_text", "launched": False}

    # ── Guard: low confidence ─────────────────────────────────────────────
    confidences = [line.get("confidence", 0.0) for line in ocr_result.get("lines", [])]
    mean_conf = sum(confidences) / len(confidences) if confidences else 1.0
    if mean_conf < min_confidence:
        print(
            f"[solver_bridge] ⚠️  OCR confidence {mean_conf:.2f} < {min_confidence} — skipping solver."
        )
        return {
            "skipped": True,
            "reason": f"low_confidence_{mean_conf:.2f}",
            "launched": False,
        }

    print(f"[solver_bridge] OCR confidence {mean_conf:.2f} ✅  Forwarding to solver...")
    print(f"[solver_bridge] Engine: {'co-scientist' if use_co_scientist else 'legacy BRAIN'}")
    print(f"[solver_bridge] OCR text preview: {text[:120].replace(chr(10),' ')} ...")

    # ── Launch ───────────────────────────────────────────────────────────────
    # Choose runner based on USE_CO_SCIENTIST flag
    if use_co_scientist:
        _runner = lambda: _run_co_scientist(text, job_path, send_to_display)
    else:
        ocr_txt_path = Path(job_path) / "ocr_text_for_solver.txt"
        ocr_txt_path.write_text(text, encoding="utf-8")
        _runner = lambda: _run_solver(text, job_path, send_to_display)

    if blocking:
        result = _runner()
        result["thread"] = "blocking"
        return result

    # Non-blocking: daemon thread so it doesn't block server shutdown
    thread_result = {"launched": False, "thread": "background",
                     "engine": "co_scientist" if use_co_scientist else "legacy"}

    def _thread_target():
        _runner()

    t = threading.Thread(target=_thread_target, daemon=True, name="solver_bridge")
    t.start()
    thread_result["launched"] = True
    print(f"[solver_bridge] 🚀 Solver launched in background thread (daemon=True).")
    return thread_result
