"""Plain-module workers for task-shaped deterministic execution.

Workers are pure functions over stored records returning typed results with sources.
"""

from picobot.workers.repo_diff_worker import RepoDiffResult, run_repo_diff_worker
from picobot.workers.session_summary_worker import SessionSummaryResult, run_session_summary_worker

__all__ = [
    "RepoDiffResult",
    "run_repo_diff_worker",
    "SessionSummaryResult",
    "run_session_summary_worker",
]
