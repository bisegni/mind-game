from __future__ import annotations

import json
from typing import Any, Mapping, Sequence


GAME_LOOP_LAYER = "You are the Mind Game story loop."
NARRATOR_VOICE_LAYER = (
    "When you narrate, stay in character, keep the voice concise, and avoid internal implementation details."
)
COMPACT_MEMORY_LAYER = (
    "Use the compact session summary and recent messages as memory; do not rely on or restate the full transcript."
)
TOOL_CONTEXT_LAYER = (
    "Use the provided tool catalog and tool results to decide the next step, and keep tool arguments small and explicit."
)
SCENE_ASCII_LAYER = (
    "For final narration JSON, include scene_ascii as 10-18 ASCII-only lines representing the current situation; "
    "make it useful for a terminal map panel with compact spatial layout, landmarks, exits, and the current player "
    "position when obvious; if scene_viewport is provided, treat its rows and cols as hard layout targets, use all "
    "available rows and columns, produce a full-canvas map instead of a boxed mini-map, fill the panel with a spatial "
    "map instead of prose or a small sketch, avoid small centered drawings, spread rooms/zones/corridors/landmarks "
    "across the full viewport including left, center, right, top, middle, and bottom, avoid legends unless they are "
    "short and do not reduce map coverage, use @ for player, ? for unknown exit, * for point of interest, =/- for "
    "corridors, # for walls or structure, and never include a title or scene name line because status already shows "
    "the scene; "
    "use no ANSI escapes and keep it sized for a terminal side pane."
)
REDRAW_ONLY_LAYER = (
    "If redraw_only is true in the snapshot, do not advance the story or change facts; "
    'reply with the "final" JSON, set content to an empty string, and produce scene_ascii '
    "freshly sized to scene_viewport.cols x scene_viewport.rows that depicts the current scene state."
)
PROMPT_ERROR_LAYER = (
    "If required state is missing or contradictory, ask one short clarifying question instead of inventing hidden state."
)


def build_system_prompt() -> str:
    return " ".join(
        [
            GAME_LOOP_LAYER,
            "Your job is to guide the player through the game while learning preferences and maintaining a stable scene.",
            "Ask one concise question at a time when you need more information.",
            "Prefer questions about tone, setting, challenge level, and desired player experience.",
            NARRATOR_VOICE_LAYER,
            SCENE_ASCII_LAYER,
            COMPACT_MEMORY_LAYER,
            TOOL_CONTEXT_LAYER,
            REDRAW_ONLY_LAYER,
            PROMPT_ERROR_LAYER,
        ]
    )


def build_turn_prompt(snapshot: Mapping[str, Any], tools: Sequence[Any]) -> str:
    sections = [
        GAME_LOOP_LAYER,
        NARRATOR_VOICE_LAYER,
        COMPACT_MEMORY_LAYER,
        _format_snapshot(snapshot),
        _format_story_graph(snapshot),
        _format_tool_catalog(tools),
        _format_tool_results(snapshot.get("observations", [])),
        TOOL_CONTEXT_LAYER,
        SCENE_ASCII_LAYER,
        REDRAW_ONLY_LAYER,
        PROMPT_ERROR_LAYER,
    ]
    return "\n".join(section for section in sections if section)


def _format_snapshot(snapshot: Mapping[str, Any]) -> str:
    compact_snapshot = {
        "turn": snapshot.get("turn", 0),
        "player_input": snapshot.get("player_input", ""),
        "current_scene_id": snapshot.get("current_scene_id"),
        "current_summary_id": snapshot.get("current_summary_id"),
        "summary_text": snapshot.get("summary_text", ""),
        "scene_ascii": snapshot.get("scene_ascii", ""),
        "scene_viewport": snapshot.get("scene_viewport", {}),
        "redraw_only": bool(snapshot.get("redraw_only", False)),
        "facts": snapshot.get("facts", {}),
        "recent_messages": snapshot.get("recent_messages", []),
        "notes": snapshot.get("notes", []),
    }
    onboarding_seed = snapshot.get("onboarding_seed")
    if onboarding_seed:
        compact_snapshot["onboarding_seed"] = {
            "onboarding_id": onboarding_seed.get("onboarding_id"),
            "session_id": onboarding_seed.get("session_id"),
            "scene_id": onboarding_seed.get("scene_id"),
            "summary_text": onboarding_seed.get("summary_text", ""),
            "facts": onboarding_seed.get("facts", {}),
            "world_tags": onboarding_seed.get("world_tags", []),
            "story_promises": onboarding_seed.get("story_promises", []),
            "starting_state": onboarding_seed.get("starting_state", {}),
            "memory_seed": onboarding_seed.get("memory_seed", {}),
        }
    return f"Compact memory: {json.dumps(compact_snapshot, sort_keys=True)}"


def _format_story_graph(snapshot: Mapping[str, Any]) -> str:
    graph_bundle = {
        "graph_focus": snapshot.get("graph_focus", {}),
        "entities": snapshot.get("entities", []),
        "edges": snapshot.get("edges", []),
        "recent_turns": snapshot.get("recent_turns", []),
    }
    return f"Graph memory: {json.dumps(graph_bundle, sort_keys=True)}"


def _format_tool_catalog(tools: Sequence[Any]) -> str:
    catalog = [
        {
            "name": getattr(tool, "name", ""),
            "description": getattr(tool, "description", ""),
        }
        for tool in tools
    ]
    return f"Tool catalog: {json.dumps(catalog, sort_keys=True)}"


def _format_tool_results(observations: Sequence[Any]) -> str:
    if not observations:
        return ""

    payload = [
        {
            "tool": _read_value(observation, "tool"),
            "result": _read_value(observation, "result"),
        }
        for observation in observations
    ]
    return f"Tool results: {json.dumps(payload, sort_keys=True)}"


def _read_value(item: Any, key: str) -> str:
    if isinstance(item, Mapping):
        return str(item.get(key, ""))
    return str(getattr(item, key, ""))


def normalize_user_input(value: str) -> str:
    return value.strip()


def is_exit_command(value: str) -> bool:
    normalized = normalize_user_input(value).lower()
    return normalized in {"exit", "quit", "bye"}
