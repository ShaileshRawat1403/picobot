from pathlib import Path
import sqlite3

import pytest

from picobot.learning.feedback import ResponseFeedbackStore


def test_feedback_is_bounded_owned_and_idempotent(tmp_path: Path):
    store = ResponseFeedbackStore(tmp_path / "workspace")

    useful = store.record(
        owner_id="web:browser:owner-a",
        session_key="web:web:owner-a:session-a",
        run_id="run-a",
        kind="useful",
    )
    assert useful.kind == "useful"
    assert useful.note is None
    assert [item.id for item in store.list("web:browser:owner-a")] == [useful.id]
    assert store.list("web:browser:owner-b") == []

    corrected = store.record(
        owner_id="web:browser:owner-a",
        session_key="web:web:owner-a:session-a",
        run_id="run-a",
        kind="correction",
        note="Prefer a concise answer with the decision first.",
    )
    assert corrected.id == useful.id
    assert corrected.kind == "correction"
    assert corrected.note == "Prefer a concise answer with the decision first."
    assert len(store.list("web:browser:owner-a")) == 1
    attached = store.attach_candidate("web:browser:owner-a", corrected.id, "memory", "memory-a")
    assert attached.candidate_type == "memory"
    assert attached.candidate_ref == "memory-a"
    assert store.attach_candidate("web:browser:owner-a", corrected.id, "memory", "memory-a").id == corrected.id
    with pytest.raises(ValueError, match="different learning candidate"):
        store.attach_candidate("web:browser:owner-a", corrected.id, "skill", "skill-a")


def test_corrections_require_bounded_notes(tmp_path: Path):
    store = ResponseFeedbackStore(tmp_path / "workspace")
    args = {
        "owner_id": "owner",
        "session_key": "session",
        "run_id": "run",
        "kind": "correction",
    }
    with pytest.raises(ValueError, match="correction note is required"):
        store.record(**args)
    with pytest.raises(ValueError, match="limited to"):
        store.record(**args, note="x" * 2_001)
    with pytest.raises(ValueError, match="must be useful"):
        store.record(
            owner_id="owner",
            session_key="session",
            run_id="run",
            kind="maybe",
        )


def test_feedback_does_not_store_response_content_or_cross_session_records(tmp_path: Path):
    store = ResponseFeedbackStore(tmp_path / "workspace")
    store.record(
        owner_id="owner",
        session_key="session-a",
        run_id="run-a",
        kind="not_useful",
        note="The answer missed the requested constraint.",
    )
    assert store.list("owner", session_key="session-a")[0].run_id == "run-a"
    assert store.list("owner", session_key="session-b") == []
    with sqlite3.connect(store.path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(response_feedback)")}
        note = connection.execute(
            "SELECT note FROM response_feedback WHERE owner_id = ?", ("owner",)
        ).fetchone()[0]
    assert note == "The answer missed the requested constraint."
    assert {"response", "transcript", "tool_arguments"}.isdisjoint(columns)
