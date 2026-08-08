"""Tests for plain-module workers in picobot.workers."""

import tempfile
from pathlib import Path

from picobot.workers.repo_diff_worker import RepoDiffResult, run_repo_diff_worker
from picobot.workers.session_summary_worker import SessionSummaryResult, run_session_summary_worker


def test_repo_diff_worker_non_git_workspace():
    with tempfile.TemporaryDirectory() as tmpdir:
        res = run_repo_diff_worker(Path(tmpdir))
        assert isinstance(res, RepoDiffResult)
        assert res.branch == "none"
        assert res.is_clean is True
        assert res.total_files_changed == 0
        assert res.to_dict()["branch"] == "none"


def test_session_summary_worker():
    events = [
        {"role": "user", "content": "Let's update the roadmap and test cases"},
        {"role": "assistant", "content": "I have created the git diff and worker modules"},
    ]
    memories = [
        {"kind": "decision", "value": "Use change-set approval"},
        {"kind": "open_question", "value": "Which key shortcut for selection capture?"},
    ]

    res = run_session_summary_worker("session-123", events, memories)
    assert isinstance(res, SessionSummaryResult)
    assert res.session_id == "session-123"
    assert res.total_turns == 2
    assert res.user_turns == 1
    assert res.assistant_turns == 1
    assert res.decisions_count == 1
    assert res.open_questions_count == 1
    assert "testing" in res.key_topics
    assert "git repository" in res.key_topics
    assert len(res.sources) == 3
