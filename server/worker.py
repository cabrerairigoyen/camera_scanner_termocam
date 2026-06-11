import json
import logging
import os
import time

from server.db import SessionLocal, init_db
from server.services.worker import WorkerService


def configure_logging():
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format='{"time":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","message":"%(message)s"}',
    )


def main():
    configure_logging()
    init_db()
    worker = WorkerService(SessionLocal)
    recovered = worker.recover()
    logging.getLogger("termocam.worker").info("worker started; recovered=%s", recovered)
    poll_seconds = float(os.getenv("WORKER_POLL_SECONDS", "1"))
    while True:
        worked = worker.run_once()
        if not worked:
            time.sleep(poll_seconds)


if __name__ == "__main__":
    main()
