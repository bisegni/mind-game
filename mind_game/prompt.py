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
PROMPT_ERROR_LAYER = (
    "If required state is missing or contradictory, ask one short clarifying question instead of inventing hidden state."
)

MAP_SYSTEM_PROMPT = (
    "You draw an ASCII tile map for the Mind Game viewport. Output ONLY raw ASCII map characters. "
    "No JSON, no markdown, no code fences, no title, no legend, no preface, no trailing prose, "
    "no commentary. Begin the response with the first line of the map and stop immediately after "
    "the final required map row."
)
MAP_INSTRUCTIONS_LAYER = (
    "Draw a tile map of the current scene that depicts what the narration just described. "
    "Every room or zone MUST have full perimeter walls (top, bottom, AND both sides) drawn with #. "
    "Place a short label inside each room as [NAME] using up to 6 uppercase letters; never overwrite "
    "a wall with a label. Connect rooms with corridors: = or - for horizontal, | for vertical, + for "
    "corners; every room must connect to at least one other room or to a marked exit. "
    "Use @ for the player (exactly one, inside a room), ? for unknown or unexplored exits, "
    "* for points of interest, . for floor inside rooms, ~ for water, space for unmapped void "
    "outside rooms. Distribute rooms across the FULL viewport: use the top, middle, and bottom "
    "thirds and the left, center, and right thirds; do not cluster all rooms in the upper half. "
    "Vary room sizes (small 5x4 up to medium 15x8) so the layout is not a single horizontal strip. "
    "For viewports >= 30x10 draw 3 to 6 labeled rooms with at least 2 connecting corridors; for "
    "smaller viewports draw 1-2 rooms filling the area. Use no ANSI escapes and no markdown. "
    "The viewport dimensions are a strict output contract, not a suggestion."
)


def build_system_prompt() -> str:
    return " ".join(
        [
            GAME_LOOP_LAYER,
            "Your job is to guide the player through the game while learning preferences and maintaining a stable scene.",
            "Ask one concise question at a time when you need more information.",
            "Prefer questions about tone, setting, challenge level, and desired player experience.",
            NARRATOR_VOICE_LAYER,
            COMPACT_MEMORY_LAYER,
            TOOL_CONTEXT_LAYER,
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
        PROMPT_ERROR_LAYER,
    ]
    return "\n".join(section for section in sections if section)


def build_map_prompt(snapshot: Mapping[str, Any], viewport: Mapping[str, int] | None = None) -> str:
    cols = int((viewport or snapshot.get("scene_viewport") or {}).get("cols") or 60)
    rows = int((viewport or snapshot.get("scene_viewport") or {}).get("rows") or 16)
    scene_summary = str(snapshot.get("summary_text") or "").strip()
    scene_id = str(snapshot.get("current_scene_id") or "").strip()
    last_player = str(snapshot.get("player_input") or "").strip()
    facts = snapshot.get("facts") or {}
    recent_messages = list(snapshot.get("recent_messages") or [])[-4:]
    context = {
        "scene_id": scene_id,
        "summary_text": scene_summary,
        "facts": facts,
        "recent_messages": recent_messages,
        "last_player_input": last_player,
        "viewport": {"cols": cols, "rows": rows},
    }
    return "\n".join(
        [
            MAP_INSTRUCTIONS_LAYER,
            f"Target viewport: EXACTLY {cols} columns by EXACTLY {rows} rows.",
            f"Output MUST contain exactly {rows} lines.",
            f"Each output line MUST contain exactly {cols} ASCII characters; pad with spaces if needed.",
            "Do not output more rows, fewer rows, wider rows, narrower rows, a title, a legend, or any prose.",
            "Use the full available viewport area while staying inside the exact row and column counts.",
            f"Scene context: {json.dumps(context, sort_keys=True)}",
            "Output the map now, raw ASCII only.",
        ]
    )


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
