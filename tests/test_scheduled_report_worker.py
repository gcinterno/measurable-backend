from threading import Event
import signal

import pytest
import app.scheduled_report_worker as worker


def test_once_dispatches_and_executes_claims(monkeypatch):
    calls = []
    claims = iter(["one", "two", None])
    monkeypatch.setattr(worker, "dispatch_due", lambda *a, **kw: calls.append("dispatch"))
    monkeypatch.setattr(worker, "claim_run", lambda *a, **kw: next(claims))
    monkeypatch.setattr(worker, "execute_run", lambda factory, claim: calls.append(claim))
    worker.run_worker(factory=object(), once=True)
    assert calls == ["dispatch", "one", "two"]


def test_graceful_shutdown_finishes_current_run_without_new_claim(monkeypatch):
    stop, calls = Event(), []
    monkeypatch.setattr(worker, "dispatch_due", lambda *a, **kw: None)
    monkeypatch.setattr(worker, "claim_run", lambda *a, **kw: "claim")
    def execute(factory, claim):
        calls.append(claim)
        stop.set()
    monkeypatch.setattr(worker, "execute_run", execute)
    worker.run_worker(factory=object(), stop=stop)
    assert calls == ["claim"]


def test_cli_signal_restored_and_options_forwarded(monkeypatch):
    original = signal.getsignal(signal.SIGTERM)
    def run(**kwargs):
        assert kwargs["once"] and kwargs["batch_size"] == 2 and kwargs["poll_interval"] == 0.5
        signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
        assert kwargs["stop"].is_set()
    monkeypatch.setattr(worker, "run_worker", run)
    worker.main(["--once", "--batch-size", "2", "--poll-interval", "0.5"])
    assert signal.getsignal(signal.SIGTERM) == original


@pytest.mark.parametrize("args", [["--batch-size", "0"], ["--poll-interval", "0"]])
def test_invalid_worker_options_rejected(args):
    with pytest.raises(SystemExit):
        worker.main(args)
