import os

import requests


class SolverUnavailable(RuntimeError):
    pass


class SolverClient:
    def __init__(self):
        self.base_url = os.getenv("SOLVER_BASE_URL", "").rstrip("/")
        self.token = os.getenv("SOLVER_SERVICE_TOKEN")
        self.timeout = int(os.getenv("SOLVER_TIMEOUT_SECONDS", "60"))

    def create_job(self, payload: dict, idempotency_key: str) -> dict:
        if not self.base_url:
            raise SolverUnavailable("SOLVER_BASE_URL is not configured")
        headers = {"Idempotency-Key": idempotency_key}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        try:
            response = requests.post(
                f"{self.base_url}/v1/solve-jobs",
                json=payload,
                headers=headers,
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise SolverUnavailable("Solver service is unavailable") from exc
        if response.status_code >= 500:
            raise SolverUnavailable(f"Solver service returned HTTP {response.status_code}")
        if response.status_code >= 400:
            raise ValueError(f"Solver rejected request with HTTP {response.status_code}")
        return response.json()
