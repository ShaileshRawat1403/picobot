from __future__ import annotations

import json

import pytest

from picobot.agent.tools.github import GitHubPullRequestTool


@pytest.mark.asyncio
async def test_github_pr_overview_is_structured_and_bounded(monkeypatch):
    tool = GitHubPullRequestTool()
    payload = {
        "title": "Improve session cockpit",
        "state": "OPEN",
        "author": {"login": "shailesh"},
        "baseRefName": "main",
        "headRefName": "feature/cockpit",
        "isDraft": False,
        "reviewDecision": "REVIEW_REQUIRED",
        "mergeable": "MERGEABLE",
        "updatedAt": "2026-08-01T10:00:00Z",
        "url": "https://github.com/acme/pico/pull/7",
        "body": "A bounded review workflow.",
    }
    seen: list[list[str]] = []

    async def fake_run(args):
        seen.append(args)
        return 0, json.dumps(payload), ""

    monkeypatch.setattr(tool, "_run", fake_run)
    result = await tool.execute(operation="overview", repo="acme/pico", number=7)

    assert "GitHub PR: acme/pico#7" in result
    assert "Improve session cockpit" in result
    assert "A bounded review workflow." in result
    assert seen == [
        [
            "pr",
            "view",
            "7",
            "--repo",
            "acme/pico",
            "--json",
            "title,state,author,baseRefName,headRefName,isDraft,reviewDecision,mergeable,url,updatedAt,body",
        ]
    ]


@pytest.mark.asyncio
async def test_github_pr_checks_and_diff_are_bounded(monkeypatch):
    tool = GitHubPullRequestTool()
    calls = []

    async def fake_run(args):
        calls.append(args)
        if args[1] == "checks":
            return 0, json.dumps([{"name": "tests", "state": "SUCCESS", "bucket": "pass", "workflow": "CI", "link": "https://example.test"}]), ""
        return 0, "x" * 20_000, ""

    monkeypatch.setattr(tool, "_run", fake_run)
    checks = await tool.execute(operation="checks", repo="acme/pico", number=8)
    diff = await tool.execute(operation="diff", repo="acme/pico", number=8)

    assert "tests: SUCCESS [pass]" in checks
    assert len(diff) <= tool._MAX_DIFF + len("\n\n[Output truncated by Pico.]")
    assert "Output truncated by Pico" in diff
    assert calls[0][:5] == ["pr", "checks", "8", "--repo", "acme/pico"]
    assert calls[1] == ["pr", "diff", "8", "--repo", "acme/pico"]


@pytest.mark.asyncio
async def test_github_pr_rejects_invalid_repo_and_does_not_run(monkeypatch):
    tool = GitHubPullRequestTool()
    called = False

    async def fake_run(_args):
        nonlocal called
        called = True
        return 0, "", ""

    monkeypatch.setattr(tool, "_run", fake_run)
    result = await tool.execute(operation="overview", repo="https://github.com/acme/pico", number=1)

    assert result.startswith("Error: repo must")
    assert called is False
