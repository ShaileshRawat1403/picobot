from pathlib import Path

import pytest

from picobot.agent.skills import SkillsLoader
from picobot.learning.store import SkillProposalStore


def test_skill_proposals_are_reviewable_owned_and_non_destructive(tmp_path: Path):
    workspace = tmp_path / "workspace"
    store = SkillProposalStore(workspace)
    proposal = store.create(
        owner_id="web:browser:owner-a",
        session_key="web:web:owner-a:session-a",
        name="website-ops-brief",
        description="Create a safe WebsiteOps brief from a reviewed request.",
        guidance="1. Gather the outcome.\n2. Produce a reviewable brief.",
    )

    assert proposal.status == "proposed"
    assert proposal.name == "website-ops-brief"
    assert "safe WebsiteOps brief" in proposal.content
    assert "session-a" not in proposal.content
    assert store.list("web:browser:owner-a") == [proposal]
    assert store.list("web:browser:owner-b") == []

    revised = store.revise(
        "web:browser:owner-a", proposal.id, proposal.content + "\n- Stop before publishing.\n"
    )
    assert "Stop before publishing" in revised.content

    approved = store.approve("web:browser:owner-a", proposal.id)
    assert approved.status == "approved"
    assert approved.installed_path == "skills/website-ops-brief/SKILL.md"
    installed = workspace / approved.installed_path
    assert installed.read_text(encoding="utf-8") == revised.content

    with pytest.raises(ValueError, match="Only proposed"):
        store.revise("web:browser:owner-a", proposal.id, "# Changed")

    collision = store.create(
        owner_id="web:browser:owner-a",
        session_key="web:web:owner-a:session-b",
        name="website-ops-brief",
        description="A duplicate name should be refused on approval.",
    )
    with pytest.raises(ValueError, match="will not overwrite"):
        store.approve("web:browser:owner-a", collision.id)


def test_skill_proposal_rejects_invalid_names_and_does_not_write_files(tmp_path: Path):
    store = SkillProposalStore(tmp_path / "workspace")
    with pytest.raises(ValueError, match="lowercase skill name"):
        store.create(
            owner_id="owner",
            session_key="session",
            name="../outside",
            description="No traversal.",
        )

    rejected = store.create(
        owner_id="owner",
        session_key="session",
        name="safe-skill",
        description="A reviewable skill.",
    )
    assert store.reject("owner", rejected.id).status == "rejected"
    assert not (store.workspace / "skills" / "safe-skill").exists()


def test_skills_loader_reads_frontmatter_metadata(tmp_path: Path):
    skill = tmp_path / "skills" / "a-skill" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text(
        "---\nname: a-skill\ndescription: A concise capability.\n---\n\n# A skill\n",
        encoding="utf-8",
    )

    assert SkillsLoader(tmp_path).get_skill_metadata("a-skill") == {
        "name": "a-skill",
        "description": "A concise capability.",
    }
