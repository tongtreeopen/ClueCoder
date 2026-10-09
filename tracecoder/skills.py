"""Discover local skills and expose their full text on demand."""

import logging
from pathlib import Path

import yaml


def parse_frontmatter(content: str) -> tuple[dict, str]:
    lines = content.splitlines()
    if not lines or lines[0] != "---":
        return {}, content
    try:
        end = lines.index("---", 1)
    except ValueError as error:
        raise ValueError("Skill frontmatter is not closed") from error
    metadata = yaml.safe_load("\n".join(lines[1:end])) or {}
    if not isinstance(metadata, dict):
        raise ValueError("Skill frontmatter must be a mapping")  # noqa: TRY004 - invalid persisted/tool data uses ValueError consistently
    return metadata, "\n".join(lines[end + 1:])


class SkillCatalog:
    def __init__(self, workspace: Path, user_root: Path | None = None):
        self.workspace = workspace.resolve()
        self.user_root = user_root or Path.home() / ".tracecoder" / "skills"
        self.skills: dict[str, tuple[str, str]] = {}
        self.scan()

    def scan(self) -> None:
        self.skills.clear()
        project_root = self.workspace / ".tracecoder" / "skills"
        for root, allowed_parent in ((self.user_root, self.user_root.parent), (project_root, self.workspace)):
            if not root.exists():
                continue
            try:
                resolved = root.resolve()
                if resolved != root.absolute() or not resolved.is_relative_to(allowed_parent.resolve()):
                    logging.getLogger(__name__).warning("Skill directory escapes its root: %s", root)
                    continue
                for manifest in sorted(root.glob("*/SKILL.md")):
                    try:
                        if not manifest.resolve().is_relative_to(resolved):
                            logging.getLogger(__name__).warning("Skill manifest escapes its root: %s", manifest)
                            continue
                        if manifest.stat().st_size > 64 * 1024:
                            logging.getLogger(__name__).warning("Skill manifest is too large: %s", manifest)
                            continue
                        content = manifest.read_text(encoding="utf-8")
                        metadata, body = parse_frontmatter(content)
                        name = metadata.get("name") or manifest.parent.name
                        body_lines = [line.strip("# ") for line in body.splitlines() if line.strip()]
                        description = metadata.get("description") or (body_lines[0] if body_lines else "")
                        if not isinstance(name, str) or not isinstance(description, str):
                            raise ValueError("Skill name and description must be strings")  # noqa: TRY004 - invalid persisted/tool data uses ValueError consistently
                        self.skills[name] = (" ".join(description.split()), content)
                    except (OSError, UnicodeError, ValueError, yaml.YAMLError) as error:
                        logging.getLogger(__name__).warning("Cannot load Skill %s: %s", manifest, error)
            except (OSError, ValueError) as error:
                logging.getLogger(__name__).warning("Cannot scan Skill directory %s: %s", root, error)

    def catalog(self) -> str:
        return "\n".join(f"- {name}: {description}" for name, (description, _) in self.skills.items())

    def load(self, name: str) -> str:
        entry = self.skills.get(name)
        return entry[1] if entry else f"Error: unknown skill {name!r}. Available: {', '.join(self.skills) or 'none'}"
