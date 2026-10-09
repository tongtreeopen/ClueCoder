"""Session-scoped tool consent.

Read-only and work-card updates bypass prompting; other tools require consent
unless explicitly allowed. Workspace, Plan Mode and execution boundaries are
enforced separately and are not disabled by consent."""


class Permission:
    """Session-scoped consent state. Pure: no I/O, the CLI hands in `ask`."""

    READ_ONLY = frozenset({"workflow_status", "read_file", "glob", "grep", "todo_write", "load_skill", "list_memories", "read_memory",
                           "list_tasks", "get_task", "search_session", "read_session_event",
                           "search_memory", "read_work_state", "background_status"})
    SESSION_STATE = frozenset({"work_update"})

    def __init__(self, ask=None, allow_all: bool = False):
        # ask(tool_name, arguments) -> "once" | "always" | "deny"
        self.ask = ask
        self.allow_all = allow_all
        self._always: set[str] = set()

    def check(self, tool_name: str, arguments: dict) -> str | None:
        """Decide one call. None lets it through; a string is the refusal
        the model receives as its tool result."""
        if tool_name in self.READ_ONLY or tool_name in self.SESSION_STATE or self.allow_all or tool_name in self._always:
            return None
        if self.ask is None:
            return (
                f"Permission denied: {tool_name} mutates state and this session is "
                "non-interactive, so nobody can approve it. Rerun with --yes, or "
                "tell the user the exact step so they can run it themselves."
            )
        verdict = self.ask(tool_name, arguments)
        if verdict == "always":
            self._always.add(tool_name)
            return None
        if verdict == "once":
            return None
        return (
            f"Permission denied: the user refused this {tool_name} call. "
            "Do not retry it unchanged; ask what they would prefer instead."
        )
