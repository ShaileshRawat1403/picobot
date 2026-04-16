"""Skill management tools."""

from typing import Any

from loguru import logger

from picobot.agent.skills import SkillsLoader
from picobot.agent.tools.base import Tool


class ListSkillsTool(Tool):
    """Tool for listing available skills."""

    name = "list_skills"
    description = "List all available skills with their descriptions and availability status."

    parameters = {
        "type": "object",
        "properties": {
            "filter": {
                "type": "string",
                "description": "Filter skills by name (case-insensitive substring)",
            },
            "show_disabled": {
                "type": "boolean",
                "description": "Include disabled skills in output",
                "default": False,
            },
        },
    }

    def __init__(self, skills_loader: SkillsLoader):
        self._loader = skills_loader

    async def execute(self, filter: str = "", show_disabled: bool = False) -> str:
        skills = self._loader.list_skills(filter_unavailable=False)
        if not skills:
            return "No skills found."

        lines = ["## Available Skills\n"]
        for s in skills:
            name = s["name"]
            if not show_disabled and not self._loader.is_skill_enabled(name):
                continue
            if filter and filter.lower() not in name.lower():
                continue

            desc = self._loader._get_skill_description(name)
            source = s["source"]
            available = self._loader._check_requirements(self._loader._get_skill_meta(name))
            params = self._loader.get_skill_params(name)

            status = "✅" if available else "❌"
            if not self._loader.is_skill_enabled(name):
                status = "🚫"

            lines.append(f"- **{name}** ({status}) [{source}]")
            lines.append(f"  {desc}")
            if params:
                lines.append(f"  Params: {params}")

        return "\n".join(lines)


class GetSkillTool(Tool):
    """Tool for loading a specific skill's full content."""

    name = "get_skill"
    description = "Get the full content of a skill for detailed reference."

    parameters = {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Skill name to retrieve",
            },
        },
        "required": ["name"],
    }

    def __init__(self, skills_loader: SkillsLoader):
        self._loader = skills_loader

    async def execute(self, name: str) -> str:
        content = self._loader.load_skill(name)
        if not content:
            return f"Skill '{name}' not found."

        content = self._loader._strip_frontmatter(content)
        params = self._loader.get_skill_params(name)
        if params:
            content = self._loader.apply_skill_parameters(content, name)

        return f"# Skill: {name}\n\n{content}"


def create_skill_tools(
    skills_loader: SkillsLoader,
) -> list[Tool]:
    """Create skill management tools."""
    return [ListSkillsTool(skills_loader), GetSkillTool(skills_loader)]
