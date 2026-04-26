from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Literal, Mapping, Protocol, Sequence

from .story_state import StoryStateStore


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
    scene_ascii: str = ""
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
    scene_ascii: str = ""


class BaseReActEngine:
    def __init__(
        self,
        reasoner: Reasoner,
        *,
        story_store: StoryStateStore | None = None,
        session_id: int | None = None,
        subagent_runner: SubagentRunner | None = None,
        tools: Sequence[Tool] | None = None,
        max_steps: int = 6,
    ) -> None:
        self._reasoner = reasoner
        self._subagent_runner = subagent_runner or DeterministicSubagentRunner()
        self._story_store = story_store
        self._story_session_id = session_id
        self._tools = list(tools or self._build_default_tools())
        self._tool_index = {tool.name: tool for tool in self._tools}
        self._max_steps = max_steps
        self.scene_viewport_size: Mapping[str, int] | None = None

        if self._story_store is not None:
            if self._story_session_id is None:
                latest_session = self._story_store.latest_playable_session()
                self._story_session_id = (
                    latest_session.id if latest_session is not None else self._story_store.create_session()
                )

            story_session = self._story_store.load_session(self._story_session_id)
            if story_session is None:
                raise KeyError(f"Unknown story session: {self._story_session_id}")

            compact_state = self._story_store.build_prompt_state(
                self._story_session_id,
                player_input="",
                observations=[],
            )
            self.session = GameSession(
                turn=int(compact_state.get("turn", story_session.current_turn)),
                facts=dict(compact_state.get("facts", {})),
                notes=list(compact_state.get("notes", [])),
            )
        else:
            self.session = GameSession()

    @property
    def tools(self) -> list[Tool]:
        return list(self._tools)

    @property
    def story_session_id(self) -> int | None:
        return self._story_session_id

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
            scene_ascii = decision.scene_ascii.strip()
            self.session.transcript.append(GameMessage(role="assistant", content=reply))
            self.session.turn += 1
            if self._story_store is not None and self._story_session_id is not None:
                self._story_store.record_turn(
                    self._story_session_id,
                    turn_number=self.session.turn - 1,
                    player_input=text,
                    narrator_output=reply,
                    prompt_state=snapshot,
                    facts=dict(self.session.facts),
                    notes=list(self.session.notes),
                    observations=observations,
                    consequences=[observation.result for observation in observations if observation.result],
                    scene_ascii=scene_ascii,
                )
            return EngineTurn(player_input=text, reply=reply, observations=observations, scene_ascii=scene_ascii)

        raise RuntimeError("ReAct loop exceeded the configured step limit")

    def redraw_scene(self, *, viewport: Mapping[str, int]) -> str:
        """Ask the reasoner for a fresh scene_ascii sized to the new viewport."""
        self.scene_viewport_size = dict(viewport)
        context = ToolContext(session=self.session, player_input="")
        snapshot = self._snapshot(context, [])
        snapshot["redraw_only"] = True
        snapshot["player_input"] = ""
        decision = self._reasoner.decide(snapshot, self._tools)
        if decision.kind != "final":
            return ""
        return decision.scene_ascii.strip()

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
        if self._story_store is not None and self._story_session_id is not None:
            snapshot = self._story_store.build_prompt_state(
                self._story_session_id,
                player_input=context.player_input,
                observations=observations,
            )
            snapshot["turn"] = self.session.turn
            snapshot["facts"] = dict(self.session.facts)
            snapshot["notes"] = list(self.session.notes[-6:])
            snapshot["player_input"] = context.player_input
            snapshot["observations"] = [
                {"tool": item.tool, "result": item.result}
                for item in observations
            ]
            snapshot["tool_catalog"] = [
                {"name": tool.name, "description": tool.description}
                for tool in self._tools
            ]
            if self.scene_viewport_size:
                snapshot["scene_viewport"] = dict(self.scene_viewport_size)
            return snapshot

        snapshot = {
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
        if self.scene_viewport_size:
            snapshot["scene_viewport"] = dict(self.scene_viewport_size)
        return snapshot

    def _dispatch_tool(self, context: ToolContext, call: ToolCall) -> str:
        tool = self._tool_index.get(call.name)
        if tool is None:
            raise KeyError(f"Unknown tool: {call.name}")

        return tool.handler(context, call.arguments)

    def _tool_session_read(self, context: ToolContext, arguments: Mapping[str, Any]) -> str:
        payload = self._snapshot(context, [])
        payload = {
            "turn": payload.get("turn", self.session.turn),
            "facts": payload.get("facts", dict(self.session.facts)),
            "notes": payload.get("notes", list(self.session.notes[-3:])),
            "player_input": payload.get("player_input", context.player_input),
            "summary_text": payload.get("summary_text", ""),
            "graph_focus": payload.get("graph_focus", {}),
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
