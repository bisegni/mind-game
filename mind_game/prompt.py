from __future__ import annotations

import json
from typing import Any, Mapping, Sequence


GAME_LOOP_LAYER = "You are the Mind Game story loop."
NARRATOR_VOICE_LAYER = (
    "When you narrate, stay in character, keep the voice concise, and avoid internal implementation details."
)
SCENE_DESCRIPTION_LAYER = (
    "For every final answer, include scene_description as compact spatial context for map creation: "
    "current location, player position, exits, nearby rooms or zones, landmarks, obstacles, "
    "interactive objects, and unexplored directions."
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
    "no commentary, no thinking preamble. "
    "Your very first output character must be the first character of map row 1. "
    "Stop immediately after the final required map row."
)
MAP_INSTRUCTIONS_LAYER = (
    "Draw a tile map of the current scene that depicts what the narration just described. "
    "Draw # only for actual room or zone walls described by the scene. Never use # as background, "
    "padding, filler, or an end-of-map curtain. Empty or unknown outside space must be spaces. "
    "Rooms or zones should have clear perimeter walls (top, bottom, and sides) drawn with # when "
    "there is enough space. "
    "Place a short label inside each room as [NAME] using up to 6 uppercase letters; never overwrite "
    "a wall with a label. Connect rooms with corridors: = or - for horizontal, | for vertical, + for "
    "corners; every room must connect to at least one other room or to a marked exit. "
    "Use @ for the player (exactly one, inside a room), ? for unknown or unexplored exits, "
    "* for points of interest, . for floor inside rooms, ~ for water, space for unmapped void "
    "outside rooms. Spread described rooms across the viewport when the scene contains multiple "
    "spaces, but leave unused viewport cells blank instead of filling them with walls. "
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
            SCENE_DESCRIPTION_LAYER,
            COMPACT_MEMORY_LAYER,
            TOOL_CONTEXT_LAYER,
            PROMPT_ERROR_LAYER,
        ]
    )


def build_turn_prompt(snapshot: Mapping[str, Any], tools: Sequence[Any]) -> str:
    sections = [
        GAME_LOOP_LAYER,
        NARRATOR_VOICE_LAYER,
        SCENE_DESCRIPTION_LAYER,
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
    scene_description = str(snapshot.get("scene_description") or "").strip()
    scene_id = str(snapshot.get("current_scene_id") or "").strip()
    last_player = str(snapshot.get("player_input") or "").strip()
    facts = snapshot.get("facts") or {}
    recent_messages = list(snapshot.get("recent_messages") or [])[-4:]
    latest_narrator = _latest_message_content(recent_messages, "assistant")
    context = {
        "scene_id": scene_id,
        "scene_description": scene_description,
        "latest_narrator_message": latest_narrator,
        "summary_text": scene_summary,
        "facts": facts,
        "recent_messages": recent_messages,
        "last_player_input": last_player,
        "viewport": {"cols": cols, "rows": rows},
    }
    return "\n".join(
        [
            "/no_think",
            MAP_INSTRUCTIONS_LAYER,
            f"Target viewport: EXACTLY {cols} columns by EXACTLY {rows} rows.",
            f"Output MUST contain exactly {rows} lines.",
            f"Each output line MUST contain exactly {cols} ASCII characters; pad with spaces if needed.",
            "Do not output more rows, fewer rows, wider rows, narrower rows, a title, a legend, or any prose.",
            "Use the full available viewport area while staying inside the exact row and column counts.",
            "Map source priority: scene_description first, then latest_narrator_message, then summary_text and facts.",
            f"Scene context: {json.dumps(context, sort_keys=True)}",
            "Output the map now. Begin with map row 1 immediately — zero preamble.",
        ]
    )


def _format_snapshot(snapshot: Mapping[str, Any]) -> str:
    compact_snapshot = {
        "turn": snapshot.get("turn", 0),
        "player_input": snapshot.get("player_input", ""),
        "current_scene_id": snapshot.get("current_scene_id"),
        "current_summary_id": snapshot.get("current_summary_id"),
        "summary_text": snapshot.get("summary_text", ""),
        "scene_description": snapshot.get("scene_description", ""),
        "scene_ascii": snapshot.get("scene_ascii", ""),
        "scene_viewport": snapshot.get("scene_viewport", {}),
        "redraw_only": bool(snapshot.get("redraw_only", False)),
        "facts": snapshot.get("facts", {}),
        "recent_messages": snapshot.get("recent_messages", []),
        "notes": snapshot.get("notes", []),
    }
    onboarding_seed = snapshot.get("onboarding_seed")
    if onboarding_seed:
        seed_block: dict[str, Any] = {
            "onboarding_id": onboarding_seed.get("onboarding_id"),
            "session_id": onboarding_seed.get("session_id"),
            "scene_id": onboarding_seed.get("scene_id"),
            "summary_text": onboarding_seed.get("summary_text", ""),
            "scene_description": onboarding_seed.get("scene_description", ""),
            "facts": onboarding_seed.get("facts", {}),
            "world_tags": onboarding_seed.get("world_tags", []),
            "story_promises": onboarding_seed.get("story_promises", []),
            "starting_state": onboarding_seed.get("starting_state", {}),
            "memory_seed": onboarding_seed.get("memory_seed", {}),
        }
        if onboarding_seed.get("lore"):
            seed_block["lore"] = onboarding_seed["lore"]
        if onboarding_seed.get("story_lines"):
            seed_block["story_lines"] = onboarding_seed["story_lines"]
        if onboarding_seed.get("key_npcs"):
            seed_block["key_npcs"] = onboarding_seed["key_npcs"]
        compact_snapshot["onboarding_seed"] = seed_block
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


def _latest_message_content(messages: Sequence[Any], role: str) -> str:
    for message in reversed(list(messages)):
        if not isinstance(message, Mapping):
            continue
        if str(message.get("role") or "") == role:
            return str(message.get("content") or "").strip()
    return ""


def normalize_user_input(value: str) -> str:
    return value.strip()


def is_exit_command(value: str) -> bool:
    normalized = normalize_user_input(value).lower()
    return normalized in {"exit", "quit", "bye"}
