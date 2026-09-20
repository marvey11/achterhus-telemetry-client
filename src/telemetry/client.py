import logging
import uuid
from datetime import UTC, datetime
from types import TracebackType
from typing import Literal, Self

import requests

logger = logging.getLogger(__name__)

type JSONValue = (
    str | int | float | bool | list["JSONValue"] | dict[str, "JSONValue"] | None
)


class TelemetryClient:
    """Telemetry reporting client for containerized Python workers."""

    def __init__(
        self,
        api_url: str,
        service_name: str,
        timeout: float = 3.0,
    ) -> None:
        self.api_url = api_url.rstrip("/")
        self.service_name = service_name
        self.timeout = timeout

        self.run_id: str = str(uuid.uuid4())
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
        """Signal job execution start to the telemetry backend."""
        self.started_at = datetime.now(UTC)
        self._send_payload(status="RUNNING")

    def finish(
        self,
        status: str = "SUCCESS",
        error_message: str | None = None,
    ) -> None:
        """Signal job execution completion to the telemetry backend."""
        self.ended_at = datetime.now(UTC)
        self._send_payload(
            status=status,
            error_message=error_message,
        )

    def _send_payload(self, status: str, error_message: str | None = None) -> None:
        """Dispatch HTTP POST payload to FastAPI backend with exception protection."""
        if self.started_at is None:
            self.started_at = datetime.now(UTC)

        duration: float | None = None
        if self.ended_at is not None:
            duration = (self.ended_at - self.started_at).total_seconds()

        payload: dict[str, JSONValue] = {
            "service_name": self.service_name,
            "run_id": self.run_id,
            "status": status,
            "started_at": self.started_at.isoformat(),
            "ended_at": self.ended_at.isoformat() if self.ended_at else None,
            "duration_seconds": duration,
            "metrics": self._metrics,
            "error_message": error_message,
            "logs_summary": self._logs_summary,
        }

        try:
            response = requests.post(
                f"{self.api_url}/api/v1/runs",
                json=payload,
                timeout=self.timeout,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            # Failure to send telemetry must never interrupt actual utility work
            logger.warning("Failed to dispatch telemetry payload: %s", exc)

    # --- Context Manager Protocol ---

    def __enter__(self) -> Self:
        self.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> Literal[False]:
        if exc_type is not None:
            # Exception occurred inside the context block
            self.finish(
                status="FAILED",
                error_message=f"{exc_type.__name__}: {exc_val}",
            )
        else:
            self.finish(status="SUCCESS")

        # Returning False re-raises exceptions so application error handling works as
        # normal
        return False
