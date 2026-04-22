from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .engine import BaseReActEngine, ReActDecision, Tool, ToolCall
from .prompt import build_system_prompt, build_turn_prompt, is_exit_command


@dataclass(slots=True)
class OllamaReActReasoner:
    model: Any
    system_prompt: str

    def decide(self, snapshot: Mapping[str, Any], tools: Sequence[Tool]) -> ReActDecision:
        from langchain_core.messages import HumanMessage, SystemMessage

        prompt = self._build_prompt(snapshot, tools)
        response = self.model.invoke(
            [
                SystemMessage(content=self.system_prompt),
                HumanMessage(content=prompt),
            ],
        )
        content = response.content if isinstance(response.content, str) else str(response.content)
        return self._parse_decision(content)

    def _build_prompt(self, snapshot: Mapping[str, Any], tools: Sequence[Tool]) -> str:
        return "\n".join(
            [
                "You are running a bounded ReAct turn for the Mind Game prototype.",
                "Choose exactly one action per response.",
                'Return JSON only as either {"kind":"tool","tool":"<name>","arguments":{...}} or {"kind":"final","content":"..."}.',
                "Use tools when you need session state or bounded delegation.",
                "Keep tool arguments small and explicit.",
                build_turn_prompt(snapshot, tools),
            ],
        )

    def _parse_decision(self, content: str) -> ReActDecision:
        text = content.strip()
        if not text.startswith("{"):
            return ReActDecision(kind="final", content=text)

        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return ReActDecision(kind="final", content=text)

        kind = str(payload.get("kind") or payload.get("type") or "final").lower()
        if kind == "tool":
            tool_name = str(payload.get("tool") or payload.get("name") or "").strip()
            arguments = payload.get("arguments") or payload.get("args") or {}
            if not isinstance(arguments, dict):
                arguments = {}
            return ReActDecision(
                kind="tool",
                tool=ToolCall(name=tool_name, arguments=dict(arguments)),
            )

        content_text = str(payload.get("content") or payload.get("final") or text).strip()
        return ReActDecision(kind="final", content=content_text)


def build_reasoner(model_name: str, base_url: str) -> OllamaReActReasoner:
    try:
        from langchain_ollama import ChatOllama
    except ModuleNotFoundError as error:  # pragma: no cover - depends on local install
        raise ModuleNotFoundError(error.name) from error

    model = ChatOllama(
        model=model_name,
        base_url=base_url,
        temperature=0.7,
        max_retries=2,
    )
    return OllamaReActReasoner(model=model, system_prompt=build_system_prompt())


def main() -> int:
    model_name = os.environ.get("OLLAMA_MODEL", "llama3.1")
    base_url = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434")

    try:
        reasoner = build_reasoner(model_name, base_url)
    except ModuleNotFoundError as error:  # pragma: no cover - depends on local install
        print(f"Missing dependency: {error}. Install with `python -m pip install -e .`.")
        return 1

    engine = BaseReActEngine(reasoner)

    print(f'Mind Game chat loop ready using Ollama model "{model_name}" at {base_url}.')
    print('Type "exit" to quit.\n')

    try:
        while True:
            user_text = input("You > ")

            if is_exit_command(user_text):
                break

            trimmed = user_text.strip()
            if not trimmed:
                continue

            result = engine.run_turn(trimmed)
            print(f"AI  > {result.reply}\n")
    except (KeyboardInterrupt, EOFError):
        print("\nGoodbye.")
    except Exception as error:  # pragma: no cover - defensive CLI guard
        print(f"Chat loop failed: {error}")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
