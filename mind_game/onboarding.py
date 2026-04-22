from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence


_NORMALIZED_KEYS = (
    "genre",
    "tone",
    "setting",
    "player_role",
    "campaign_goal",
    "difficulty",
    "must_have_constraints",
    "must_avoid_constraints",
    "world_tags",
    "opening_hook",
    "starting_state",
    "story_promises",
    "memory_seed",
)


@dataclass(frozen=True, slots=True)
class OnboardingQuestion:
    key: str
    prompt: str


_QUESTIONNAIRE: tuple[OnboardingQuestion, ...] = (
    OnboardingQuestion(
        key="genre",
        prompt="What kind of story should this be?",
    ),
    OnboardingQuestion(
        key="tone",
        prompt="What tone should it have?",
    ),
    OnboardingQuestion(
        key="setting",
        prompt="Where does it begin?",
    ),
    OnboardingQuestion(
        key="player_role",
        prompt="Who is the player in this world?",
    ),
    OnboardingQuestion(
        key="campaign_goal",
        prompt="What should the story be trying to achieve?",
    ),
    OnboardingQuestion(
        key="difficulty",
        prompt="How hard should the game feel?",
    ),
)


def _text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        return value or None
    text = str(value).strip()
    return text or None


def _listify(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        items = [part.strip() for part in re.split(r"[,\n]", value) if part.strip()]
        return items or ([value.strip()] if value.strip() else [])
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray, str)):
        items: list[str] = []
        for item in value:
            text = _text(item)
            if text is not None:
                items.append(text)
        return items
    text = _text(value)
    return [text] if text is not None else []


def _mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    text = _text(value)
    if text is None:
        return {}
    return {"text": text}


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "seed"


def get_onboarding_questions() -> list[OnboardingQuestion]:
    return list(_QUESTIONNAIRE)


def get_onboarding_question_order() -> list[str]:
    return [question.key for question in _QUESTIONNAIRE]


def normalize_onboarding_setup(
    raw_answers: Mapping[str, Any],
    *,
    question_order: Sequence[str] = (),
) -> dict[str, Any]:
    setup: dict[str, Any] = {}
    alias_map = {
        "genre": ("genre", "story_genre", "genre_choice"),
        "tone": ("tone", "tone_choice"),
        "setting": ("setting", "world", "starting_setting"),
        "player_role": ("player_role", "role", "character_role"),
        "campaign_goal": ("campaign_goal", "goal", "story_goal"),
        "difficulty": ("difficulty", "challenge", "pace"),
        "must_have_constraints": ("must_have_constraints", "must_have", "requirements"),
        "must_avoid_constraints": ("must_avoid_constraints", "must_avoid", "avoid"),
        "world_tags": ("world_tags", "tags", "keywords"),
        "opening_hook": ("opening_hook", "hook", "opening"),
        "starting_state": ("starting_state", "start_state", "state"),
        "story_promises": ("story_promises", "promises"),
        "memory_seed": ("memory_seed", "seed", "notes"),
    }

    for key in _NORMALIZED_KEYS:
        raw_value = None
        for alias in alias_map.get(key, (key,)):
            if alias in raw_answers:
                raw_value = raw_answers[alias]
                break

        if key in {"must_have_constraints", "must_avoid_constraints", "world_tags", "story_promises"}:
            setup[key] = _listify(raw_value)
        elif key == "starting_state":
            setup[key] = _mapping(raw_value)
        elif key == "memory_seed":
            seed = _mapping(raw_value)
            if question_order:
                seed.setdefault("question_order", list(question_order))
            setup[key] = seed
        else:
            setup[key] = _text(raw_value)

    if not setup["world_tags"]:
        derived_tags = [setup.get("genre"), setup.get("tone"), setup.get("setting")]
        setup["world_tags"] = [tag for tag in derived_tags if tag]

    if not setup["story_promises"]:
        promises = [value for value in [setup.get("campaign_goal"), setup.get("opening_hook")] if value]
        setup["story_promises"] = promises

    if not setup["memory_seed"]:
        setup["memory_seed"] = {
            "question_order": list(question_order),
            "answer_keys": [key for key in question_order if key in raw_answers],
        }
    else:
        setup["memory_seed"].setdefault("question_order", list(question_order))
        setup["memory_seed"].setdefault("answer_keys", [key for key in question_order if key in raw_answers])

    return setup


def build_onboarding_seed_scene(
    normalized_setup: Mapping[str, Any],
    *,
    session_id: int | None = None,
    onboarding_id: int | None = None,
) -> dict[str, Any]:
    genre = _text(normalized_setup.get("genre"))
    tone = _text(normalized_setup.get("tone"))
    setting = _text(normalized_setup.get("setting"))
    player_role = _text(normalized_setup.get("player_role"))
    campaign_goal = _text(normalized_setup.get("campaign_goal"))
    opening_hook = _text(normalized_setup.get("opening_hook"))
    difficulty = _text(normalized_setup.get("difficulty"))
    world_tags = _listify(normalized_setup.get("world_tags"))
    must_have_constraints = _listify(normalized_setup.get("must_have_constraints"))
    must_avoid_constraints = _listify(normalized_setup.get("must_avoid_constraints"))
    story_promises = _listify(normalized_setup.get("story_promises"))
    starting_state = _mapping(normalized_setup.get("starting_state"))
    memory_seed = _mapping(normalized_setup.get("memory_seed"))

    if opening_hook:
        summary_text = opening_hook
    else:
        summary_bits = ["Opening scene"]
        if setting:
            summary_bits.append(f"in {setting}")
        if player_role:
            summary_bits.append(f"where you play as {player_role}")
        if campaign_goal:
            summary_bits.append(f"while pursuing {campaign_goal}")
        summary_text = ", ".join(summary_bits)
        if not summary_text.endswith("."):
            summary_text += "."

    title_source = opening_hook or setting or campaign_goal or genre or "Opening"
    scene_slug = _slugify(title_source)
    scene_id_bits = ["scene", "onboarding"]
    if onboarding_id is not None:
        scene_id_bits.append(str(onboarding_id))
    elif session_id is not None:
        scene_id_bits.append(str(session_id))
    scene_id_bits.append(scene_slug)

    facts = {
        "genre": genre,
        "tone": tone,
        "setting": setting,
        "player_role": player_role,
        "campaign_goal": campaign_goal,
        "difficulty": difficulty,
    }

    return {
        "scene_id": ":".join(scene_id_bits),
        "title": title_source,
        "summary_text": summary_text,
        "opening_prompt": summary_text,
        "facts": {key: value for key, value in facts.items() if value},
        "world_tags": world_tags,
        "must_have_constraints": must_have_constraints,
        "must_avoid_constraints": must_avoid_constraints,
        "story_promises": story_promises,
        "starting_state": starting_state,
        "memory_seed": memory_seed,
        "session_id": session_id,
        "onboarding_id": onboarding_id,
        "normalized_setup": dict(normalized_setup),
    }
