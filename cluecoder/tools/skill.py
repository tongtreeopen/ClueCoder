"""Read a discovered Skill by name."""

from typing import ClassVar

from ..skills import SkillCatalog
from .base import Tool


class LoadSkillTool(Tool):
    name = "load_skill"
    description = "Load the complete instructions for a listed Skill by name."
    parameters: ClassVar = {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}

    def __init__(self, catalog: SkillCatalog):
        self.catalog = catalog

    def execute(self, name: str) -> str:
        return self.catalog.load(name)
