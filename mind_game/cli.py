from __future__ import annotations

import json
import os
import sys
import shutil
import time
from datetime import datetime, timezone
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from .engine import BaseReActEngine, ReActDecision, Tool, ToolCall
from .console import ConsoleMessage, load_session_messages, render_message_batch, render_session_history, stream_message
from .scene_renderer import render_scene_frame
from .shell import SceneFrame as ShellSceneFrame, ShellMode, ShellStatus, render_split_pane
from .onboarding import (
    OllamaOnboardingReasoner,
    build_onboarding_prompt_state,
    build_onboarding_seed_scene,
    normalize_onboarding_setup,
    required_field_from_setup,
    required_field_prompt,
    REQUIRED_ONBOARDING_FIELDS,
)
from .prompt import build_system_prompt, build_turn_prompt, is_exit_command
from .story_state import OnboardingSessionRecord, StoryStateStore


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
                'Return JSON only as either {"kind":"tool","tool":"<name>","arguments":{...}} or {"kind":"final","content":"...","scene_ascii":"..."}.',
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
        scene_ascii = str(payload.get("scene_ascii") or "").strip()
        return ReActDecision(kind="final", content=content_text, scene_ascii=scene_ascii)


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


def build_onboarding_reasoner(model_name: str, base_url: str) -> OllamaOnboardingReasoner:
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
    return OllamaOnboardingReasoner(model=model)


def build_story_store() -> StoryStateStore:
    db_path = os.environ.get("MIND_GAME_STORY_DB_PATH")
    if not db_path:
        return StoryStateStore(default_story_db_path())
    return StoryStateStore(db_path)


def default_story_db_path() -> Path:
    return Path(__file__).resolve().parents[1] / ".mind_game.sqlite3"


def main() -> int:
    model_name = os.environ.get("OLLAMA_MODEL", "llama3.1")
    base_url = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434")

    try:
        reasoner = build_reasoner(model_name, base_url)
        onboarding_reasoner = build_onboarding_reasoner(model_name, base_url)
    except ModuleNotFoundError as error:  # pragma: no cover - depends on local install
        print(f"Missing dependency: {error}. Install with `python -m pip install -e .`.")
        return 1

    story_store = build_story_store()
    try:
        story_session_id, onboarding_session = _resolve_story_session(story_store)
        if story_store is not None and onboarding_session is not None:
            story_session_id = _run_onboarding_chat(story_store, onboarding_session, onboarding_reasoner)

        engine = BaseReActEngine(reasoner, story_store=story_store, session_id=story_session_id)

        if sys.stdout.isatty() and os.environ.get("MIND_GAME_LEGACY_CLI", "").lower() not in {"1", "true", "yes"}:
            try:
                from .tui import MindGameApp
            except ModuleNotFoundError as error:  # pragma: no cover - depends on local install
                print(f"Missing dependency: {error}. Install with `python -m pip install -e .`.")
                return 1

            MindGameApp(engine=engine, story_store=story_store, model_name=model_name, base_url=base_url).run()
            return 0

        print(f'Mind Game chat loop ready using Ollama model "{model_name}" at {base_url}.')
        print('Type "exit" to quit.\n')

        if story_store is not None and engine.story_session_id is not None:
            _print_session_history(story_store, engine.story_session_id)
            _print_opening_scene(story_store, engine.story_session_id)
            _print_console_shell(
                story_store,
                engine.story_session_id,
                model_name=model_name,
                use_color=_console_use_color(),
            )

        while True:
            user_text = input("Player > ")

            if is_exit_command(user_text):
                break

            trimmed = user_text.strip()
            if not trimmed:
                continue

            if story_store is not None and engine.story_session_id is not None:
                _print_console_shell(
                    story_store,
                    engine.story_session_id,
                    model_name=model_name,
                    mode="spinner",
                    message="waiting for model reply",
                    spinner_index=engine.session.turn,
                    use_color=_console_use_color(),
                )

            try:
                result = engine.run_turn(trimmed)
            except Exception as error:
                if story_store is not None and engine.story_session_id is not None:
                    _print_console_shell(
                        story_store,
                        engine.story_session_id,
                        model_name=model_name,
                        mode="error",
                        error=str(error),
                        use_color=_console_use_color(),
                    )
                raise
            _print_turn_messages(story_store, engine.story_session_id, trimmed, result.reply)
            if story_store is not None and engine.story_session_id is not None:
                mode = "tool_call" if result.observations else "idle"
                tool_name = result.observations[-1].tool if result.observations else None
                _print_console_shell(
                    story_store,
                    engine.story_session_id,
                    model_name=model_name,
                    mode=mode,
                    tool_name=tool_name,
                    message="turn complete" if result.observations else "ready",
                    use_color=_console_use_color(),
                )
    except (KeyboardInterrupt, EOFError):
        print("\nGoodbye.")
    except Exception as error:  # pragma: no cover - defensive CLI guard
        print(f"Chat loop failed: {error}")
        return 1
    finally:
        if story_store is not None:
            story_store.close()

    return 0


def _resolve_story_session(story_store: StoryStateStore | None) -> tuple[int | None, OnboardingSessionRecord | None]:
    if story_store is None:
        return None, None

    onboarding_sessions = story_store.list_sessions(statuses=("onboarding",))
    if onboarding_sessions:
        return _choose_onboarding_session(story_store, onboarding_sessions)

    playable_session = story_store.latest_playable_session()
    if playable_session is not None:
        return playable_session.id, None

    return _create_new_onboarding_session(story_store)


def _choose_onboarding_session(
    story_store: StoryStateStore,
    onboarding_sessions: Sequence[Any],
) -> tuple[int | None, OnboardingSessionRecord | None]:
    print("Open onboarding sessions:")
    for index, session in enumerate(onboarding_sessions, start=1):
        created = session.created_at.replace("T", " ", 1)
        updated = session.updated_at.replace("T", " ", 1)
        print(
            f"  {index}. session {session.id} | {session.status} | "
            f"created {created} | updated {updated}",
        )

    while True:
        choice = input("Choose a session number, press Enter for the newest, or 'n' for new onboarding: ").strip().lower()
        if choice in {"", "r", "resume"}:
            selected_session = onboarding_sessions[0]
            break
        if choice in {"n", "new"}:
            return _create_new_onboarding_session(story_store)
        if choice.isdigit():
            selected_index = int(choice) - 1
            if 0 <= selected_index < len(onboarding_sessions):
                selected_session = onboarding_sessions[selected_index]
                break
        print("Please choose a listed session, press Enter, or type 'n'.")

    onboarding_session = story_store.load_session_onboarding(selected_session.id)
    if onboarding_session is None:
        onboarding_session = story_store.create_onboarding_session(
            selected_session.id,
            question_order=REQUIRED_ONBOARDING_FIELDS,
            status="in_progress",
        )
    return selected_session.id, onboarding_session


def _create_new_onboarding_session(story_store: StoryStateStore) -> tuple[int | None, OnboardingSessionRecord]:
    session_id = story_store.create_session(status="onboarding")
    onboarding_session = story_store.create_onboarding_session(
        session_id,
        question_order=REQUIRED_ONBOARDING_FIELDS,
        status="in_progress",
    )
    return session_id, onboarding_session


def _run_onboarding_chat(
    story_store: StoryStateStore,
    onboarding_session: OnboardingSessionRecord,
    onboarding_reasoner: OllamaOnboardingReasoner,
) -> int:
    current_session = onboarding_session
    use_color = _console_use_color()
    if current_session.answers:
        stream_message(
            ConsoleMessage(
                role="narrator",
                content="Resuming the story setup.",
                turn_number=len(current_session.answers),
                created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            ),
            use_color=use_color,
        )
    else:
        stream_message(
            ConsoleMessage(
                role="narrator",
                content="Let's build your story together.",
                turn_number=0,
                created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            ),
            use_color=use_color,
        )

    while True:
        snapshot = build_onboarding_prompt_state(current_session)
        missing_field = required_field_from_setup(snapshot)
        if missing_field is None:
            normalized_setup = normalize_onboarding_setup(current_session.normalized_setup, question_order=REQUIRED_ONBOARDING_FIELDS)
            seed_scene = build_onboarding_seed_scene(
                normalized_setup,
                session_id=current_session.session_id,
                onboarding_id=current_session.id,
            )
            completed = story_store.complete_onboarding_session(
                current_session.id,
                normalized_setup=normalized_setup,
                generated_summary_text=_fallback_onboarding_completion_text(normalized_setup),
                seed_scene=seed_scene,
            )
            return completed.session_id

        attempt_count = sum(1 for answer in current_session.answers if answer.question_key == missing_field)
        question_text = onboarding_reasoner.next_question(snapshot, missing_field=missing_field, attempt_count=attempt_count).strip()
        if not question_text:
            question_text = required_field_prompt(missing_field, attempt_count=attempt_count)
        stream_message(
            ConsoleMessage(
                role="narrator",
                content=question_text,
                turn_number=len(current_session.answers),
                created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            ),
            use_color=use_color,
        )
        answer = input("Player > ").strip()
        if not answer:
            stream_message(
                ConsoleMessage(
                    role="narrator",
                    content=required_field_prompt(missing_field, attempt_count=attempt_count + 1),
                    turn_number=len(current_session.answers),
                    created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                ),
                use_color=use_color,
            )
            continue

        extracted_updates = onboarding_reasoner.extract_updates(snapshot, answer_text=answer, asked_field=missing_field)
        merged_setup = dict(current_session.normalized_setup)
        if extracted_updates:
            merged_setup.update(extracted_updates)
            normalized_setup = normalize_onboarding_setup(merged_setup, question_order=REQUIRED_ONBOARDING_FIELDS)
            answer_index = len(current_session.answers)
            for offset, field in enumerate(REQUIRED_ONBOARDING_FIELDS):
                if field not in extracted_updates:
                    continue
                story_store.record_onboarding_answer(
                    current_session.id,
                    question_key=field,
                    question_text=required_field_prompt(field, attempt_count=answer_index + offset),
                    answer_index=answer_index + offset,
                    raw_answer_text=answer,
                    normalized_answer={field: extracted_updates[field]},
                )
            if missing_field not in extracted_updates:
                story_store.record_onboarding_answer(
                    current_session.id,
                    question_key=missing_field,
                    question_text=question_text,
                    answer_index=answer_index + len(extracted_updates),
                    raw_answer_text=answer,
                    normalized_answer={},
                )
        else:
            normalized_setup = normalize_onboarding_setup(merged_setup, question_order=REQUIRED_ONBOARDING_FIELDS)
            story_store.record_onboarding_answer(
                current_session.id,
                question_key=missing_field,
                question_text=question_text,
                answer_index=len(current_session.answers),
                raw_answer_text=answer,
                normalized_answer={},
            )
        current_session = story_store.update_onboarding_session(
            current_session.id,
            status="in_progress",
            normalized_setup=normalized_setup,
            question_order=REQUIRED_ONBOARDING_FIELDS,
        )


def _fallback_onboarding_completion_text(normalized_setup: Mapping[str, Any]) -> str:
    parts = ["Great, I have enough to start."]
    genre = str(normalized_setup.get("genre") or "").strip()
    tone = str(normalized_setup.get("tone") or "").strip()
    setting = str(normalized_setup.get("setting") or "").strip()
    player_role = str(normalized_setup.get("player_role") or "").strip()
    if genre:
        parts.append(f"This will be a {genre} story.")
    if tone:
        parts.append(f"It should feel {tone}.")
    if setting:
        parts.append(f"It begins in {setting}.")
    if player_role:
        parts.append(f"You will play as {player_role}.")
    return " ".join(parts)


def _print_session_history(story_store: StoryStateStore, session_id: int) -> None:
    use_color = _console_use_color()
    if not story_store.list_turns(session_id, limit=1):
        return
    history = render_session_history(story_store, session_id, use_color=use_color)
    if history.strip():
        print(history, end="")


def _print_turn_messages(story_store: StoryStateStore, session_id: int, player_input: str, narrator_output: str) -> None:
    use_color = _console_use_color()
    if story_store is not None and session_id is not None:
        messages = load_session_messages(story_store, session_id, limit=1)
    else:
        created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        messages = [
            ConsoleMessage(role="player", content=player_input, turn_number=0, created_at=created_at),
            ConsoleMessage(role="narrator", content=narrator_output, turn_number=0, created_at=created_at),
        ]

    if messages:
        player_message = next((message for message in messages if message.role == "player"), None)
        narrator_message = next((message for message in messages if message.role == "narrator"), None)
        if player_message is not None:
            print(render_message_batch([player_message], use_color=use_color))
        if narrator_message is not None:
            stream_message(narrator_message, use_color=use_color)
            print()


def _print_opening_scene(story_store: StoryStateStore, session_id: int) -> None:
    use_color = _console_use_color()
    session = story_store.load_session(session_id)
    onboarding = story_store.load_session_onboarding(session_id)
    if session is None or onboarding is None or session.current_turn != 0:
        return

    opening_text = str(
        onboarding.seed_scene.get("opening_prompt")
        or onboarding.seed_scene.get("summary_text")
        or onboarding.generated_summary_text
        or "",
    ).strip()
    if not opening_text:
        return

    stream_message(
        ConsoleMessage(
            role="narrator",
            content=opening_text,
            turn_number=0,
            created_at=session.created_at,
            scene_id=session.current_scene_id,
        ),
        use_color=use_color,
    )
    print()


# The CLI treats the split-pane as a view over story state: transcript history stays
# in the text log, while the shell is redrawn from the latest session snapshot.
def _print_console_shell(
    story_store: StoryStateStore,
    session_id: int | None,
    *,
    model_name: str,
    use_color: bool,
    movie_frames: int = 1,
    mode: ShellMode = "idle",
    message: str | None = None,
    tool_name: str | None = None,
    error: str | None = None,
    spinner_index: int = 0,
) -> None:
    should_animate = movie_frames > 1 and sys.stdout.isatty()
    if not should_animate:
        shell_output = _render_console_shell(
            story_store,
            session_id,
            model_name=model_name,
            use_color=use_color,
            mode=mode,
            message=message,
            tool_name=tool_name,
            error=error,
            spinner_index=spinner_index,
            frame_index=0,
        )
        if shell_output:
            print(shell_output)
        return

    print("\x1b[s", end="")
    for frame_index in range(movie_frames):
        shell_output = _render_console_shell(
            story_store,
            session_id,
            model_name=model_name,
            use_color=use_color,
            mode=mode,
            message=message,
            tool_name=tool_name,
            error=error,
            spinner_index=spinner_index + frame_index,
            frame_index=frame_index,
        )
        if not shell_output:
            continue
        print("\x1b[u" + shell_output, end="")
        if frame_index < movie_frames - 1:
            time.sleep(0.05)
    print()


def _render_console_shell(
    story_store: StoryStateStore,
    session_id: int | None,
    *,
    model_name: str,
    use_color: bool,
    frame_index: int = 0,
    mode: ShellMode = "idle",
    message: str | None = None,
    tool_name: str | None = None,
    error: str | None = None,
    spinner_index: int = 0,
) -> str:
    if session_id is None:
        return ""

    width, height = _console_dimensions()
    rail_width = 24
    if width <= rail_width + 1:
        return ""

    status = _build_shell_status(
        story_store,
        session_id,
        model_name=model_name,
        mode=mode,
        message=message,
        tool_name=tool_name,
        error=error,
        spinner_index=spinner_index,
    )
    scene_frame = _build_shell_scene_frame(
        story_store,
        session_id,
        width=width - rail_width - 1,
        height=height,
        use_color=use_color,
        frame_index=frame_index,
    )
    return render_split_pane(status, scene_frame, width=width, height=height, rail_width=rail_width, use_color=use_color)


def _build_shell_status(
    story_store: StoryStateStore,
    session_id: int,
    *,
    model_name: str,
    mode: ShellMode,
    message: str | None,
    tool_name: str | None,
    error: str | None,
    spinner_index: int,
) -> ShellStatus:
    session = _load_shell_session(story_store, session_id)
    scene_id = getattr(session, "current_scene_id", None) if session is not None else None
    turn_number = getattr(session, "current_turn", None) if session is not None else None
    if scene_id is None:
        snapshot = _load_shell_snapshot(story_store, session_id)
        scene_id = getattr(snapshot, "scene_id", None) if snapshot is not None else None

    return ShellStatus(
        mode=mode,
        turn_number=turn_number,
        scene_id=scene_id,
        model_name=model_name,
        tool_name=tool_name,
        message=message,
        error=error,
        spinner_index=spinner_index,
    )


def _build_shell_scene_frame(
    story_store: StoryStateStore,
    session_id: int,
    *,
    width: int,
    height: int,
    use_color: bool,
    frame_index: int,
) -> ShellSceneFrame:
    snapshot = _load_shell_snapshot(story_store, session_id)
    session = _load_shell_session(story_store, session_id)

    if snapshot is not None:
        frame = render_scene_frame(snapshot, width=width, height=height, use_color=use_color, frame_index=frame_index)
        return ShellSceneFrame(title=None, subtitle=None, lines=frame.lines)

    scene_id = getattr(session, "current_scene_id", None) if session is not None else None
    session_turn = getattr(session, "current_turn", None) if session is not None else None
    title = scene_id or f"Session {session_id}"
    subtitle = "Scene snapshot unavailable yet."
    lines = (
        f"scene: {scene_id or 'unknown'}",
        f"turn: {session_turn if session_turn is not None else 'idle'}",
        "Waiting for the next scene snapshot.",
    )
    return ShellSceneFrame(title=title, subtitle=subtitle, lines=lines)


def _load_shell_session(story_store: StoryStateStore, session_id: int) -> Any | None:
    loader = getattr(story_store, "load_session", None)
    if callable(loader):
        session = loader(session_id)
        if session is not None:
            return session

    fallback = getattr(story_store, "latest_playable_session", None)
    if callable(fallback):
        session = fallback()
        if session is not None and getattr(session, "id", session_id) == session_id:
            return session
    return None


def _load_shell_snapshot(story_store: StoryStateStore, session_id: int) -> Any | None:
    loader = getattr(story_store, "latest_snapshot", None)
    if not callable(loader):
        return None
    return loader(session_id)


def _console_use_color() -> bool:
    return sys.stdout.isatty() and not os.environ.get("NO_COLOR")


def _console_dimensions() -> tuple[int, int]:
    size = shutil.get_terminal_size(fallback=(96, 20))
    width = max(48, size.columns)
    height = max(12, min(size.lines - 6, 20))
    return width, height


if __name__ == "__main__":
    raise SystemExit(main())
