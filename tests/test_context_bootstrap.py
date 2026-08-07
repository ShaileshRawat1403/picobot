"""Tests for bounded bootstrap-file loading in the system prompt."""

from picobot.agent.context import ContextBuilder


def test_bootstrap_files_under_budget_pass_through_unchanged(tmp_path):
    (tmp_path / "AGENTS.md").write_text("One standing rule.", encoding="utf-8")
    (tmp_path / "SOUL.md").write_text("Be helpful.", encoding="utf-8")

    prompt = ContextBuilder(tmp_path).build_system_prompt()

    assert "## AGENTS.md\n\nOne standing rule." in prompt
    assert "## SOUL.md\n\nBe helpful." in prompt
    assert "[cut:" not in prompt


def test_oversized_bootstrap_file_keeps_head_and_tail_with_a_cut_marker(tmp_path):
    limit = ContextBuilder._BOOTSTRAP_PER_FILE_LIMIT
    body = "s" * (limit + 2_000)
    content = "HEAD-MARKER " + body
    # A unique token planted inside the region that must be elided.
    content = content[:6_000] + "MIDDLE-MARKER-UNIQUE" + content[6_000:]
    content += " TAIL-MARKER"

    section = ContextBuilder(tmp_path)._bounded_file_section("USER.md", content)

    assert "## USER.md" in section
    assert "HEAD-MARKER" in section
    assert "TAIL-MARKER" in section
    assert "[cut: USER.md" in section
    assert "chars elided] ..." in section
    assert "MIDDLE-MARKER-UNIQUE" not in section


def test_bootstrap_files_together_are_capped_by_total_budget(tmp_path):
    total = ContextBuilder._BOOTSTRAP_TOTAL_LIMIT
    for filename in ContextBuilder.BOOTSTRAP_FILES:
        (tmp_path / filename).write_text("X" * (total // 2), encoding="utf-8")

    bootstrap = ContextBuilder(tmp_path)._load_bootstrap_files()

    assert "[cut: bootstrap files exceed" in bootstrap
    assert "## AGENTS.md" in bootstrap
    assert bootstrap.endswith("X")
    assert len(bootstrap) < total + 500
