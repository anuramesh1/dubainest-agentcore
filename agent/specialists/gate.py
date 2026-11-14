"""The output gate (spec §5): everything a Strands agent writes passes through
here before it reaches the browser.

1. Narration before or between tool calls ("Let me search…") is held and
   dropped when a tool call follows; each tool call sends a `status` event.
2. Once tools have run, the answer is released sentence by sentence (or line by
   line, for tables and bullets). Each piece is checked first: every AED figure
   must appear in a tool result this turn or in the visitor's own words.
3. A piece that fails is never sent. The gate sends a safe line instead and
   stops; the caller cancels the agent.
4. The <<SUGGESTIONS>> block is held back and becomes the `suggestions` event.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from typing import Any

from ..grounding import ungrounded
from ..prompts import SUGGESTIONS_MARKER
from .tools import STATUS

log = logging.getLogger(__name__)

STOP_LINE = (
    "\n\n_I couldn't verify one of the figures for this answer, so I've stopped here. "
    "Please check the listing page, or ask me again._"
)
# A sentence ends at . ! ? followed by whitespace, or at a newline.
_BOUNDARY = re.compile(r"(?<=[.!?])[ \t]+|\n")


def parse_suggestions(tail: str) -> list[str]:
    out: list[str] = []
    for raw in tail.replace("|", "\n").splitlines():
        line = raw.strip().lstrip("-*•0123456789.) ").strip().strip('"')
        if line and "<<" not in line:
            out.append(line)
        if len(out) == 3:
            break
    return out


class OutputGate:
    def __init__(
        self,
        emit: Callable[[dict[str, Any]], None],
        facts: dict[str, Any],
        user_text: str,
        context: dict[str, Any] | None = None,
    ):
        self.emit = emit
        self.facts = facts  # live: tools add to it while the agent runs
        self.context = context or {}  # open listing, profile: also allowed sources
        self.user_text = user_text
        self.turn = ""  # text of the current model turn
        self.sent = 0  # how much of `turn` has been released
        self.tool_used = False
        self.answer = ""
        self.suggestions: list[str] = []
        self.stopped = False
        self.blocked: list[str] = []
        self.dropped_narration = 0
        self._tool_ids: set[str] = set()
        self._statuses: set[str] = set()

    # ── events from the Strands stream ───────────────────────────────────────

    def on_text(self, delta: str) -> None:
        if self.stopped:
            return
        self.turn += delta
        if self.tool_used:
            self._release(final=False)

    def on_tool(self, use: dict[str, Any]) -> None:
        if self.stopped:
            return
        tid = use.get("toolUseId") or ""
        if tid in self._tool_ids:
            return
        self._tool_ids.add(tid)
        self._drop_turn()
        self.tool_used = True
        label = STATUS.get(use.get("name", ""), "Working on it…")
        if label not in self._statuses:
            self._statuses.add(label)
            self.emit({"event_type": "status", "content": label})

    def on_turn_end(self, had_tool_use: bool) -> None:
        if self.stopped:
            return
        if had_tool_use:
            self._drop_turn()
        else:
            self._release(final=True)
            self.turn, self.sent = "", 0

    def finish(self) -> None:
        """End of stream: release anything a final turn left (e.g. a turn limit)."""
        if not self.stopped and self.turn[self.sent :].strip():
            self._release(final=True)
        if self.suggestions and not self.stopped:
            self.emit({"event_type": "suggestions", "content": self.suggestions})

    # ── internals ────────────────────────────────────────────────────────────

    def _drop_turn(self) -> None:
        if self.turn[self.sent :].strip():
            self.dropped_narration += 1
        self.turn, self.sent = "", 0

    def _release(self, final: bool) -> None:
        pending = self.turn[self.sent :]
        marker = pending.find(SUGGESTIONS_MARKER)
        cut = pending.find("<<")  # never release a (partial) marker
        limit = len(pending) if cut == -1 else cut
        if final:
            end = limit
            if marker != -1:
                self.suggestions = parse_suggestions(pending[marker + len(SUGGESTIONS_MARKER) :])
            elif cut != -1:
                end = len(pending)  # "<<" that never became a marker is just text
        else:
            ends = [m.end() for m in _BOUNDARY.finditer(pending, 0, limit)]
            end = ends[-1] if ends else 0
        if end <= 0:
            return
        piece = pending[:end]
        if final and marker != -1:
            piece = piece.rstrip()
        bad = ungrounded(piece, {"tools": self.facts, **self.context}, self.user_text)
        if bad:
            self.stopped = True
            self.blocked = bad
            log.warning("output gate blocked ungrounded figures: %s", bad)
            self.emit({"event_type": "chunk", "content": STOP_LINE})
            self.answer += STOP_LINE
            return
        if not final:
            # Keep trailing blank lines pending: they go out with the next text, so an
            # answer never ends in stray newlines before the suggestions.
            piece = piece.rstrip()
            end = len(piece)
        if piece:
            if not self.answer:
                piece = piece.lstrip()
            if piece:
                self.emit({"event_type": "chunk", "content": piece})
                self.answer += piece
        self.sent += end
