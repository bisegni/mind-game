from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Literal, Mapping, Protocol, Sequence


Role = Literal["player", "assistant", "tool"]
DecisionKind = Literal["tool", "final"]


@dataclass(slots=True)
class GameMessage:
    role: Role
    content: str


@dataclass(slots=True)
class ToolCall:
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ReActDecision:
    kind: DecisionKind
    content: str = ""
    tool: ToolCall | None = None


@dataclass(slots=True)
class ToolObservation:
    tool: str
    result: str


@dataclass(slots=True)
class GameSession:
    turn: int = 0
    facts: dict[str, str] = field(default_factory=dict)
    transcript: list[GameMessage] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ToolContext:
    session: GameSession
    player_input: str


@dataclass(frozen=True, slots=True)
class Tool:
    name: str
    description: str
    handler: Callable[[ToolContext, Mapping[str, Any]], str]


@dataclass(frozen=True, slots=True)
class SubagentTask:
    role: str
    task: str
    context: Mapping[str, Any]


class SubagentRunner(Protocol):
    def run(self, task: SubagentTask) -> str:
        ...


class Reasoner(Protocol):
    def decide(self, snapshot: Mapping[str, Any], tools: Sequence[Tool]) -> ReActDecision:
        ...


@dataclass(slots=True)
class EngineTurn:
    player_input: str
    reply: str
    observations: list[ToolObservation]


class BaseReActEngine:
    def __init__(
        self,
        reasoner: Reasoner,
        *,
        subagent_runner: SubagentRunner | None = None,
        tools: Sequence[Tool] | None = None,
        max_steps: int = 6,
    ) -> None:
        self._reasoner = reasoner
        self._subagent_runner = subagent_runner or DeterministicSubagentRunner()
        self.session = GameSession()
        self._tools = list(tools or self._build_default_tools())
        self._tool_index = {tool.name: tool for tool in self._tools}
        self._max_steps = max_steps

    @property
    def tools(self) -> list[Tool]:
        return list(self._tools)

    def run_turn(self, player_input: str) -> EngineTurn:
        text = player_input.strip()
        if not text:
            raise ValueError("player_input must not be empty")

        self.session.transcript.append(GameMessage(role="player", content=text))
        context = ToolContext(session=self.session, player_input=text)
        observations: list[ToolObservation] = []

        for _ in range(self._max_steps):
            snapshot = self._snapshot(context, observations)
            decision = self._reasoner.decide(snapshot, self._tools)

            if decision.kind == "tool":
                if decision.tool is None:
                    raise ValueError("tool decisions must include a tool call")

                result = self._dispatch_tool(context, decision.tool)
                observations.append(ToolObservation(tool=decision.tool.name, result=result))
                continue

            reply = decision.content.strip()
            self.session.transcript.append(GameMessage(role="assistant", content=reply))
            self.session.turn += 1
            return EngineTurn(player_input=text, reply=reply, observations=observations)

        raise RuntimeError("ReAct loop exceeded the configured step limit")

    def _build_default_tools(self) -> list[Tool]:
        return [
            Tool(
                name="session.read",
                description="Return a compact snapshot of the current session state.",
                handler=self._tool_session_read,
            ),
            Tool(
                name="session.write_fact",
                description="Store a short fact in the shared session state.",
                handler=self._tool_session_write_fact,
            ),
            Tool(
                name="session.add_note",
                description="Append a compact note to session memory.",
                handler=self._tool_session_add_note,
            ),
            Tool(
                name="subagent.delegate",
                description="Delegate a bounded task to a focused subagent.",
                handler=self._tool_delegate_subagent,
            ),
        ]

    def _snapshot(self, context: ToolContext, observations: Sequence[ToolObservation]) -> dict[str, Any]:
        return {
            "turn": self.session.turn,
            "player_input": context.player_input,
            "facts": dict(self.session.facts),
            "recent_messages": [
                {"role": message.role, "content": message.content}
                for message in self.session.transcript[-6:]
            ],
            "notes": list(self.session.notes[-6:]),
            "observations": [
                {"tool": item.tool, "result": item.result}
                for item in observations
            ],
            "tool_catalog": [
                {"name": tool.name, "description": tool.description}
                for tool in self._tools
            ],
        }

    def _dispatch_tool(self, context: ToolContext, call: ToolCall) -> str:
        tool = self._tool_index.get(call.name)
        if tool is None:
            raise KeyError(f"Unknown tool: {call.name}")

        return tool.handler(context, call.arguments)

    def _tool_session_read(self, context: ToolContext, arguments: Mapping[str, Any]) -> str:
        payload = {
            "turn": self.session.turn,
            "facts": dict(self.session.facts),
            "notes": list(self.session.notes[-3:]),
            "player_input": context.player_input,
        }
        return json.dumps(payload, sort_keys=True)

    def _tool_session_write_fact(self, context: ToolContext, arguments: Mapping[str, Any]) -> str:
        key = str(arguments.get("key", "")).strip()
        value = str(arguments.get("value", "")).strip()
        if not key:
            raise ValueError("session.write_fact requires a non-empty key")
        self.session.facts[key] = value
        return json.dumps({"stored": key, "value": value}, sort_keys=True)

    def _tool_session_add_note(self, context: ToolContext, arguments: Mapping[str, Any]) -> str:
        note = str(arguments.get("note", "")).strip()
        if not note:
            raise ValueError("session.add_note requires a non-empty note")
        self.session.notes.append(note)
        return json.dumps({"added": note}, sort_keys=True)

    def _tool_delegate_subagent(self, context: ToolContext, arguments: Mapping[str, Any]) -> str:
        task = str(arguments.get("task", "")).strip()
        role = str(arguments.get("role", "general")).strip() or "general"
        if not task:
            raise ValueError("subagent.delegate requires a non-empty task")

        bounded_context = arguments.get("context")
        if not isinstance(bounded_context, Mapping):
            bounded_context = {
                "player_input": context.player_input,
                "facts": dict(self.session.facts),
                "notes": list(self.session.notes[-3:]),
            }

        result = self._subagent_runner.run(
            SubagentTask(role=role, task=task, context=bounded_context),
        )
        self.session.notes.append(f"{role}: {result}")
        return result


class DeterministicSubagentRunner:
    def run(self, task: SubagentTask) -> str:
        preview = json.dumps(task.context, sort_keys=True)
        return f"{task.role} handled {task.task} with {preview[:120]}"

