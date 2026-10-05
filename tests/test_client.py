from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest
import requests

from telemetry.client import TelemetryClient

RUN_ID = "a0eebc99-9c2b-4d02-8ec7-3bb7f6f01d2b"


def make_client(monkeypatch: pytest.MonkeyPatch) -> TelemetryClient:
    monkeypatch.setenv("SERVICE_RUN_ID", RUN_ID)
    return TelemetryClient("http://telemetry-api:8000/", "newsletter-worker")


def test_client_requires_a_valid_orchestrator_run_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SERVICE_RUN_ID", raising=False)
    with pytest.raises(ValueError, match="SERVICE_RUN_ID must be set"):
        TelemetryClient("http://telemetry-api:8000", "newsletter-worker")
    with pytest.raises(ValueError, match="valid UUID"):
        TelemetryClient(
            "http://telemetry-api:8000",
            "newsletter-worker",
            run_id="not-a-uuid",
        )


def test_status_updates_use_patch_and_orchestrator_run_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = make_client(monkeypatch)
    client.set_metric("items_processed", 8)
    client.set_logs_summary("Processing complete")
    timestamp = datetime(2026, 9, 29, 10, 15, tzinfo=UTC)
    response = MagicMock()

    with patch("telemetry.client.requests.patch", return_value=response) as send:
        client.start()
        client.update_status("RUNNING", timestamp=timestamp)
        client.finish("SUCCESS")

    assert send.call_count == 3
    first_call = send.call_args_list[0]
    assert first_call.args[0] == (
        f"http://telemetry-api:8000/api/v1/runs/{RUN_ID}/status"
    )
    assert first_call.kwargs["json"]["status"] == "INITIALIZING"
    assert first_call.kwargs["json"]["source"] == "application"
    assert send.call_args_list[1].kwargs["json"]["timestamp"] == timestamp.isoformat()
    assert send.call_args_list[2].kwargs["json"]["metrics"] == {"items_processed": 8}
    assert send.call_args_list[2].kwargs["json"]["logs_summary"] == (
        "Processing complete"
    )
    assert response.raise_for_status.call_count == 3


def test_finish_without_start_reports_initialization_and_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = make_client(monkeypatch)
    with patch("telemetry.client.requests.patch") as send:
        client.finish("FAILED", error_message="worker crashed")

    assert [call.kwargs["json"]["status"] for call in send.call_args_list] == [
        "INITIALIZING",
        "FAILED",
    ]
    assert send.call_args_list[1].kwargs["json"]["error_details"] == {
        "message": "worker crashed"
    }


def test_send_event_uses_run_scoped_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = make_client(monkeypatch)
    timestamp = datetime(2026, 9, 29, 10, 15, tzinfo=UTC)
    with patch("telemetry.client.requests.post") as send:
        client.send_event(
            "checkpoint",
            {"step": "download"},
            source="application",
            timestamp=timestamp,
        )

    send.assert_called_once()
    assert send.call_args.args[0] == (
        f"http://telemetry-api:8000/api/v1/runs/{RUN_ID}/events"
    )
    assert send.call_args.kwargs["json"] == {
        "event_type": "checkpoint",
        "source": "application",
        "timestamp": timestamp.isoformat(),
        "details": {"step": "download"},
    }


def test_context_manager_reports_failure_and_preserves_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = make_client(monkeypatch)
    with patch("telemetry.client.requests.patch") as send:
        with pytest.raises(RuntimeError, match="boom"):
            with client:
                raise RuntimeError("boom")

    assert [call.kwargs["json"]["status"] for call in send.call_args_list] == [
        "INITIALIZING",
        "RUNNING",
        "FAILED",
    ]
    assert send.call_args_list[2].kwargs["json"]["error_details"]["message"] == (
        "RuntimeError: boom"
    )


def test_telemetry_transport_failure_is_logged_without_raising(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = make_client(monkeypatch)
    with patch(
        "telemetry.client.requests.patch",
        side_effect=requests.ConnectionError("unavailable"),
    ):
        client.start()

    assert "Failed to dispatch telemetry status" in caplog.text
