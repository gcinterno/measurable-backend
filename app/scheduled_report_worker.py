"""Run with python -m app.scheduled_report_worker; never uses the legacy Job/SQS path."""
import argparse
import json
import logging
import os
import signal
import socket
from threading import Event
from uuid import uuid4

from .db import SessionLocal
from .scheduled_report_execution import claim_run, dispatch_due, execute_run
from .scheduled_report_delivery import claim_delivery, execute_delivery

logger = logging.getLogger(__name__)


class WorkerLogFormatter(logging.Formatter):
    def format(self, record):
        fields = ("workspace_id", "schedule_id", "scheduled_report_run_id", "trigger_type", "attempt_number", "stage", "result", "report_type",
                  "report_id", "report_version_id", "artifact_id", "delivery_id", "ses_message_id")
        return json.dumps({"event": record.getMessage(), "level": record.levelname,
                           **{field: getattr(record, field) for field in fields if hasattr(record, field)}})


def run_worker(factory=SessionLocal, *, worker_id=None, poll_interval=5, batch_size=20, once=False, stop=None):
    stop = stop or Event()
    worker_id = worker_id or f"{socket.gethostname()}:{os.getpid()}:{uuid4().hex[:8]}"
    while not stop.is_set():
        try:
            dispatch_due(factory, batch_size=batch_size)
            for _ in range(batch_size):
                if stop.is_set():
                    break
                claim = claim_run(factory, worker_id=worker_id)
                if claim is None:
                    break
                # SIGTERM stops new claims; the current run drains with heartbeats.
                # A forced process kill is recovered by the durable lease/fence.
                execute_run(factory, claim)
            for _ in range(batch_size):
                if stop.is_set():
                    break
                delivery = claim_delivery(factory)
                if delivery is None:
                    break
                execute_delivery(factory, delivery)
        except Exception:
            logger.error("scheduled_worker_iteration_failed")
            if once:
                raise
        if once:
            return
        stop.wait(poll_interval)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Durable Schedule Reports worker")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--poll-interval", type=float, default=5)
    parser.add_argument("--batch-size", type=int, default=20)
    parser.add_argument("--worker-id")
    args = parser.parse_args(argv)
    if args.poll_interval <= 0 or not 1 <= args.batch_size <= 100:
        parser.error("poll-interval must be positive and batch-size must be 1-100")
    handler = logging.StreamHandler()
    handler.setFormatter(WorkerLogFormatter())
    logging.basicConfig(level=logging.INFO, handlers=[handler])
    stop = Event()
    previous = {}
    for name in (signal.SIGTERM, signal.SIGINT):
        previous[name] = signal.signal(name, lambda *_: stop.set())
    try:
        run_worker(worker_id=args.worker_id, poll_interval=args.poll_interval, batch_size=args.batch_size, once=args.once, stop=stop)
    finally:
        for name, old_handler in previous.items():
            signal.signal(name, old_handler)


if __name__ == "__main__":
    main()
