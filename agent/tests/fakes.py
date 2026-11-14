"""Fakes for the two model-backed pieces, shared by agent/ and api/ tests.

FakeAgent stands in for a Strands Agent: it emits the same stream_async events
(data, current_tool_use, message, result) and calls the REAL tools it was given,
so ToolBox facts, the output gate and memory are exercised exactly as in
production. No AWS.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from agent.llm import Route

Step = tuple  # ("text", str) | ("tool", name, {args}) | ("raise", Exception)


def fake_classify(intent, second=None, confidence=0.9, calls=None):
    async def classify(system, user):
        if calls is not None:
            calls.append(user)
        return Route(intent=intent, second_intent=second, confidence=confidence), {
            "model": "fake-router",
            "input_tokens": 10,
            "output_tokens": 5,
        }

    return classify


class FakeAgent:
    def __init__(self, steps: list[Step], system: str, tools: list, messages: list):
        self.steps = steps
        self.system = system
        self.tools = {t.tool_name: t for t in tools}
        self.messages = messages
        self.prompt: Any = None
        self.cancelled = False

    async def stream_async(self, prompt: Any, **kwargs: Any):
        self.prompt = prompt
        cancel = kwargs.get("cancel_signal")
        cycles, text = 1, ""
        for n, step in enumerate(self.steps):
            if cancel is not None and cancel.is_set():
                self.cancelled = True
                break
            if step[0] == "raise":
                raise step[1]
            if step[0] == "text":
                for i in range(0, len(step[1]), 7):  # 7-char pieces split words and markers
                    yield {"data": step[1][i : i + 7]}
                    text += step[1][i : i + 7]
                    if cancel is not None and cancel.is_set():
                        self.cancelled = True
                        break
            elif step[0] == "tool":
                name, args = step[1], step[2]
                use = {"toolUseId": f"tool-{n}", "name": name, "input": args}
                yield {"current_tool_use": use}
                content = [{"text": text}] if text else []
                yield {"message": {"role": "assistant", "content": [*content, {"toolUse": use}]}}
                result = self.tools[name](**args)
                yield {
                    "message": {
                        "role": "user",
                        "content": [{"toolResult": {"content": [{"json": result}]}}],
                    }
                }
                cycles, text = cycles + 1, ""
        if not self.cancelled:
            yield {"message": {"role": "assistant", "content": [{"text": text}]}}
        metrics = SimpleNamespace(
            cycle_count=cycles, accumulated_usage={"inputTokens": 100 * cycles, "outputTokens": 50}
        )
        yield {
            "result": SimpleNamespace(
                stop_reason="cancelled" if self.cancelled else "end_turn", metrics=metrics
            )
        }


class AgentFactory:
    """make_agent for build_graph(): each call scripts the next agent from `script`
    (a list of steps, or a function of the tool names → steps)."""

    def __init__(self, script):
        self.script = script
        self.made: list[FakeAgent] = []

    def __call__(self, system: str, tools: list, messages: list) -> FakeAgent:
        names = [t.tool_name for t in tools]
        steps = self.script(names) if callable(self.script) else self.script
        agent = FakeAgent(list(steps), system, tools, messages)
        self.made.append(agent)
        return agent
