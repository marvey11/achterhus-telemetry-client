# Achterhus Telemetry Client

Python client for reporting application status changes and structured events to the
Achterhus Telemetry API. Run creation and orchestration statuses belong to the
orchestrator; the client reports activity for the run assigned to its container.

## Requirements

- Python 3.12 or newer
- An API URL reachable from the service container
- A `SERVICE_RUN_ID` UUID assigned by the orchestrator

## Usage

```python
from telemetry.client import TelemetryClient

client = TelemetryClient(
    api_url="http://telemetry-api:8000",
    service_name="newsletter-worker",
)

with client:
    client.set_metric("items_processed", 42)
    client.set_logs_summary("Processed the current batch")
    client.send_event(
        "batch_completed",
        {"batch_size": 42},
    )
```

The context manager reports `INITIALIZING` and then `RUNNING` on entry, followed by
`SUCCESS` on a normal exit or `FAILED` when an exception escapes the block. The
exception is not suppressed. `SERVICE_RUN_ID` must match the run previously
registered by the orchestrator. Alternatively, callers can pass `run_id` explicitly
to the constructor.

Use `update_status()` for intermediate application transitions, `finish()` to report
completion, and `send_event()` for additional run-scoped events. A transport error is
logged and does not interrupt the service's work.

## Development

Install dependencies and run checks with `uv`:

```bash
uv sync --locked --all-extras --dev
uv run ruff check .
uv run ruff format --check .
uv run mypy .
uv run pytest
```
