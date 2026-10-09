"""CLI-only review policy for the turn-level memory workflow."""

import threading
import time
from collections.abc import Callable

from .extraction_queue import ExtractionQueue
from .memory_workflow import MemoryCandidate, MemoryWorkflow


class MemoryCLI:
    def __init__(
        self,
        workflow: MemoryWorkflow,
        base_context: Callable[[], str],
        notify: Callable[[str], None],
        ask: Callable[[MemoryCandidate], bool] | None = None,
        ask_merge: Callable[[], bool] | None = None,
        queue: ExtractionQueue | None = None,
        skill_context: Callable[[], str] | None = None,
    ):
        self.workflow = workflow
        self.base_context = base_context
        self.notify = notify
        self.ask = ask
        self.ask_merge = ask_merge
        self.queue = queue
        self.skill_context = skill_context
        self.auto = queue is not None
        self.recall_enabled = True
        self.reading_enabled = True
        self.on_reading_change: Callable[[bool], None] | None = None
        self.journal = None
        self._turn_start = None
        self._last_activity = 0.0
        self._worker: threading.Thread | None = None
        self.recalled = ""
        self.disabled = None
        self._last_proposed_version = None
        try:
            self.workflow.store.recover_pending()
        except (OSError, ValueError) as error:
            self.disabled = str(error)
            self.notify(f"Memory disabled: {error}")

    def context(self) -> str:
        if not self.reading_enabled:
            return self.skill_context() if self.skill_context else ""
        return "\n\n".join(part for part in (self.base_context(), self.recalled) if part)

    def begin_turn(self, user_text: str) -> None:
        self.recalled = ""
        if self.queue is not None and self.journal is not None:
            self._turn_start = len(self.journal.events) + 1
        if self.disabled or not self.recall_enabled:
            return
        try:
            self.recalled = self.workflow.begin_turn(user_text)
        except (OSError, ValueError) as error:
            self.notify(f"Memory recall skipped: {error}")

    def after_turn(self, agent, user_text: str, interactive: bool) -> None:
        if self.disabled or agent.plan_mode or not agent.last_turn_completed:
            return
        if self.queue is not None:
            try:
                journal = agent.work_state.journal
                self.queue.auto = self.auto
                self.queue.enqueue(journal, self._turn_start or 1, len(journal.events))
                self._last_activity = time.monotonic()
                if interactive:
                    self.start_background()
                else:
                    self.queue.process_pending(limit=3, preferred_id=journal.id)
            except (OSError, ValueError) as error:
                self.notify(f"Memory extraction queue failed: {error}")
            return
        try:
            result = self.workflow.finish_turn(user_text, agent.messages[-1].get("content") or "")
        except (OSError, ValueError) as error:
            self.notify(f"Memory extraction skipped: {error}")
            return
        if result.warning:
            self.notify(result.warning)
        saved = False
        for candidate in result.candidates:
            detail = (
                f"Memory candidate [{candidate.type}] {candidate.name}: {candidate.description}\n"
                f"{candidate.content}"
            )
            if candidate.action == "update":
                detail += f"\nPrevious: {candidate.previous}"
            self.notify(detail)
            if candidate.action == "update":
                if not interactive or self.ask is None or not self.ask(candidate):
                    self.notify(f"Memory {candidate.name} needs confirmation; not saved")
                    continue
                approved = True
            elif self.auto:
                approved = True
            else:
                approved = interactive and self.ask is not None and self.ask(candidate)
            if approved:
                outcome = self.workflow.save(
                    candidate, approve_replace=candidate.action == "update",
                    mode="auto" if self.auto and candidate.action == "create" else "reviewed",
                )
                self.notify(outcome)
                saved |= outcome.startswith("Saved")
            elif not interactive:
                self.notify(f"Memory {candidate.name} not saved (confirmation unavailable)")
        if saved:
            self.review_consolidation(agent, interactive)

    def review_consolidation(self, agent, interactive: bool, explicit: bool = False) -> None:
        if self.disabled or agent.plan_mode:
            self.notify("Memory consolidation unavailable in plan mode or while memory is disabled")
            return
        try:
            version = self.workflow.store.version()
            if not explicit and version == self._last_proposed_version:
                return
            proposal, warning = self.workflow.propose_consolidation()
        except (OSError, ValueError) as error:
            self.notify(f"Consolidation skipped: {error}")
            return
        if warning:
            self.notify(warning)
        if proposal is None:
            return
        self._last_proposed_version = version
        self.notify("Memory consolidation proposal:\n" + "\n".join(proposal.details))
        if interactive and self.ask_merge is not None and self.ask_merge():
            self.notify(self.workflow.apply_consolidation(proposal))
        else:
            self.notify("Memory consolidation not applied (confirmation required)")

    def set_auto(self, enabled: bool) -> str:
        self.auto = enabled
        if self.queue is not None:
            self.queue.auto = enabled
        return f"Memory auto-save {'on' if enabled else 'off'} for this CLI process"

    def start_background(self, delay: float = 15.0) -> None:
        if self.queue is None or not self.queue.enabled or (self._worker and self._worker.is_alive()):
            return

        def work():
            while True:
                while time.monotonic() - self._last_activity < delay:
                    time.sleep(min(1.0, delay))
                last_activity = self._last_activity
                try:
                    self.queue.process_pending(limit=3)
                except (OSError, ValueError) as error:
                    self.notify(f"Memory extraction deferred: {error}")
                if self._last_activity == last_activity:
                    break

        self._worker = threading.Thread(target=work, name="cluecoder-memory-extraction", daemon=True)
        self._worker.start()

    def set_extraction(self, enabled: bool) -> str:
        if self.queue is None:
            return "Memory extraction queue unavailable"
        self.queue.enabled = enabled
        return f"Memory extraction {'on' if enabled else 'off'} for this CLI process"

    def set_reading(self, enabled: bool) -> str:
        self.reading_enabled = enabled
        if self.on_reading_change is not None:
            self.on_reading_change(enabled)
        return f"Memory reading {'on' if enabled else 'off'} for this CLI process"

    def undo(self, agent) -> str:
        if agent.plan_mode:
            return "Error: plan mode does not allow memory undo"
        if self.disabled:
            return f"Error: memory disabled: {self.disabled}"
        return self.queue.undo_last() if self.queue is not None else self.workflow.store.undo_last()

    def handle_command(self, user_input: str, agent, emit: Callable[[object], None]) -> None:
        if user_input == "/memory auto on":
            emit(self.set_auto(True))
        elif user_input == "/memory auto off":
            emit(self.set_auto(False))
        elif user_input == "/memory undo":
            emit(self.undo(agent))
        elif user_input == "/memory status":
            emit(self.queue.status() if self.queue else "Memory queue unavailable")
        elif user_input == "/memory review":
            for item in self.queue.pending_reviews() if self.queue else []:
                emit(f"  {item['id']} [{item['scope']}] {item['name']}: {item['description']}")
        elif user_input.startswith("/memory review "):
            review_id = user_input.split(maxsplit=2)[2]
            item = (
                next((entry for entry in self.queue.pending_reviews() if entry["id"] == review_id), None)
                if self.queue
                else None
            )
            emit(item if item else "Unknown pending memory review")
        elif user_input.startswith(("/memory accept ", "/memory reject ")):
            if agent.plan_mode:
                emit("Memory review writes are unavailable in plan mode")
            elif self.queue is None:
                emit("Memory review queue unavailable")
            else:
                _, command, review_id = user_input.split(maxsplit=2)
                try:
                    emit(self.queue.accept(review_id) if command == "accept" else self.queue.reject(review_id))
                except (OSError, ValueError) as error:
                    emit(f"Error: {error}")
        elif user_input in {"/memory extraction on", "/memory extraction off"}:
            emit(self.set_extraction(user_input.endswith("on")))
        elif user_input in {"/memory reading on", "/memory reading off"}:
            enabled = user_input.endswith("on")
            emit(self.set_reading(enabled))
        elif user_input == "/memory process":
            if agent.plan_mode:
                emit("Memory processing is unavailable in plan mode")
            elif self.queue is None:
                emit("Memory extraction queue unavailable")
            else:
                emit(f"Processed {self.queue.process_pending(limit=3)} extraction job(s)")
        elif user_input == "/memory consolidate":
            self.review_consolidation(agent, interactive=True, explicit=True)
        else:
            emit(
                "Usage: /memory status|review [id]|accept ID|reject ID|process|auto on|off|"
                "extraction on|off|reading on|off|undo|consolidate"
            )
