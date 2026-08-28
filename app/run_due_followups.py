"""Run one bounded batch of persisted due Sales follow-ups."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import Sequence
from uuid import UUID

from sqlmodel import Session

from app.config import Settings, get_settings
from app.database_resilience import (
    DatabaseStartupRetryPolicy,
    ensure_database_schema_current_with_startup_retry,
)
from app.db import create_app_engine
from app.observability import configure_logging, log_structured_event
from app.runtime import ProductionRuntimeValidator
from app.services.due_follow_up_runner import (
    DEFAULT_DUE_FOLLOW_UP_LIMIT,
    MAX_DUE_FOLLOW_UP_LIMIT,
    DueFollowUpRunner,
)

runner_cli_logger = logging.getLogger("app.due_follow_up_runner.cli")


def main(
    argv: Sequence[str] | None = None,
    *,
    settings: Settings | None = None,
    session: Session | None = None,
) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.run_due_followups",
        description="Materialize one bounded batch of due Sales follow-up WorkItems.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_DUE_FOLLOW_UP_LIMIT,
        choices=range(1, MAX_DUE_FOLLOW_UP_LIMIT + 1),
        metavar=f"1-{MAX_DUE_FOLLOW_UP_LIMIT}",
        help="maximum number of due tasks to scan",
    )
    parser.add_argument(
        "--workspace-id",
        type=UUID,
        default=None,
        help="optionally restrict the run to one workspace UUID",
    )
    args = parser.parse_args(argv)
    resolved_settings = settings or get_settings()
    configure_logging(
        level=resolved_settings.log_level,
        log_format=resolved_settings.log_format,
    )

    try:
        ProductionRuntimeValidator(resolved_settings).validate()
        if resolved_settings.environment == "production":
            ensure_database_schema_current_with_startup_retry(
                resolved_settings.database_url,
                DatabaseStartupRetryPolicy(
                    max_attempts=resolved_settings.database_startup_max_attempts,
                    retry_delay_seconds=(
                        resolved_settings.database_startup_retry_delay_seconds
                    ),
                ),
            )
        if session is not None:
            summary = DueFollowUpRunner(session).run(
                workspace_id=args.workspace_id,
                limit=args.limit,
            )
        else:
            engine = create_app_engine(resolved_settings.database_url)
            try:
                with Session(engine) as runner_session:
                    summary = DueFollowUpRunner(runner_session).run(
                        workspace_id=args.workspace_id,
                        limit=args.limit,
                    )
            finally:
                engine.dispose()
    except Exception:  # noqa: BLE001 - CLI must fail with bounded, secret-safe output.
        log_structured_event(
            runner_cli_logger,
            "due_follow_up_cli_failed",
            reason="runner_system_error",
        )
        print(
            json.dumps({"status": "failed", "reason": "runner_system_error"}),
            file=sys.stderr,
        )
        return 1

    print(json.dumps({"status": "ok", **summary.as_dict()}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
