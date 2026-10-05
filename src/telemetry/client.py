import logging
import os
import uuid
from datetime import UTC, datetime
from types import TracebackType
from typing import Literal, Self

import requests

logger = logging.getLogger(__name__)

type JSONValue = (
    str | int | float | bool | list["JSONValue"] | dict[str, "JSONValue"] | None
)
type RunStatus = Literal[
    "SCHEDULED",
    "IMAGE_PULLING",
    "STARTING",
    "INITIALIZING",
    "RUNNING",
    "SUCCESS",
    "FAILED",
    "CREATE_FAILED",
    "START_FAILED",
    "OOM_KILLED",
    "TIMEOUT",
    "ORCHESTRATOR_ERROR",
]
type EventSource = Literal["orchestrator", "application", "watchguard", "api"]


class TelemetryClient:
    """Report application status and events for an orchestrated service run."""

    def __init__(
        self,
        api_url: str,
        service_name: str,
        timeout: float = 3.0,
        run_id: str | None = None,
    ) -> None:
        configured_run_id = run_id or os.getenv("SERVICE_RUN_ID")
        if not configured_run_id:
            raise ValueError(
                "SERVICE_RUN_ID must be set by the orchestrator or passed as run_id"
            )
        try:
            self.run_id = str(uuid.UUID(configured_run_id))
        except ValueError as exc:
            raise ValueError("SERVICE_RUN_ID must be a valid UUID") from exc

        self.api_url = api_url.rstrip("/")
        self.service_name = service_name
        self.timeout = timeout
        self.started_at: datetime | None = None
        self.ended_at: datetime | None = None
        self._metrics: dict[str, JSONValue] = {}
        self._logs_summary: str | None = None

    def set_metric(self, key: str, value: JSONValue) -> None:
        """Add or update a metric payload value."""
        self._metrics[key] = value

    def set_metrics(self, metrics: dict[str, JSONValue]) -> None:
        """Update multiple metric payload values at once."""
        self._metrics.update(metrics)

    def set_logs_summary(self, summary: str) -> None:
        """Attach a summary or tail of execution logs."""
        self._logs_summary = summary

    def start(self) -> None:
        """Signal that the application has started initialising."""
        self.started_at = datetime.now(UTC)
        self.update_status("INITIALIZING", timestamp=self.started_at)

    def update_status(
        self,
        status: RunStatus,
        *,
        error_details: dict[str, JSONValue] | None = None,
        timestamp: datetime | None = None,
    ) -> None:
        """Report an application status update for the orchestrated run."""
        event_timestamp = timestamp or datetime.now(UTC)
        if self.started_at is None and status in {"INITIALIZING", "RUNNING"}:
            self.started_at = event_timestamp
        self._send_status(status, event_timestamp, error_details)

    def finish(
        self,
        status: Literal["SUCCESS", "FAILED"] = "SUCCESS",
        error_message: str | None = None,
        error_details: dict[str, JSONValue] | None = None,
    ) -> None:
        """Signal application completion and include optional failure details."""
        if self.started_at is None:
            self.start()
        self.ended_at = datetime.now(UTC)
        if error_details is None and error_message is not None:
            error_details = {"message": error_message}
        self.update_status(
            status,
            error_details=error_details,
            timestamp=self.ended_at,
        )

    def send_event(
        self,
        event_type: str,
        details: dict[str, JSONValue] | None = None,
        *,
        source: EventSource = "application",
        timestamp: datetime | None = None,
    ) -> None:
        """Send a structured event associated with this service run."""
        payload: dict[str, JSONValue] = {
            "event_type": event_type,
            "source": source,
            "timestamp": (timestamp or datetime.now(UTC)).isoformat(),
            "details": details or {},
        }
        try:
            response = requests.post(
                f"{self.api_url}/api/v1/runs/{self.run_id}/events",
                json=payload,
                timeout=self.timeout,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            logger.warning("Failed to dispatch telemetry event: %s", exc)

    def _send_status(
        self,
        status: RunStatus,
        timestamp: datetime,
        error_details: dict[str, JSONValue] | None,
    ) -> None:
        payload: dict[str, JSONValue] = {
            "status": status,
            "source": "application",
            "timestamp": timestamp.isoformat(),
            "metrics": self._metrics,
        }
        if self._logs_summary is not None:
            payload["logs_summary"] = self._logs_summary
        if error_details is not None:
            payload["error_details"] = error_details

        try:
            response = requests.patch(
                f"{self.api_url}/api/v1/runs/{self.run_id}/status",
                json=payload,
                timeout=self.timeout,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            logger.warning("Failed to dispatch telemetry status: %s", exc)

    def __enter__(self) -> Self:
        self.start()
        self.update_status("RUNNING")
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> Literal[False]:
        _ = exc_tb
        if exc_type is not None:
            self.finish(
                status="FAILED",
                error_message=f"{exc_type.__name__}: {exc_val}",
            )
        else:
            self.finish(status="SUCCESS")
        return False
