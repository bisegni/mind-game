from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

_THINK_COMPLETE_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_THINK_OPEN_RE = re.compile(r"<think>.*$", re.DOTALL | re.IGNORECASE)


def _strip_thinking_tags(text: str) -> str:
    text = _THINK_COMPLETE_RE.sub("", text)
    text = _THINK_OPEN_RE.sub("", text)
    return text.lstrip("\n")


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


@dataclass(frozen=True, slots=True)
class OnboardingDecision:
    kind: str
    content: str
    updates: dict[str, Any] = field(default_factory=dict)
    setup: dict[str, Any] = field(default_factory=dict)


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

REQUIRED_ONBOARDING_FIELDS = ("genre", "tone", "setting", "player_role")
_STARTING_FIELDS = REQUIRED_ONBOARDING_FIELDS
_ADDITIONAL_FIELDS = ("player_role", "campaign_goal", "difficulty", "must_have_constraints", "must_avoid_constraints")


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


def _looks_structured(text: str) -> bool:
    stripped = text.strip()
    return stripped.startswith("{") or stripped.startswith("[") or ("\n" in stripped and "}" in stripped)


def _coerce_message_text(value: Any, fallback: str) -> str:
    if isinstance(value, str):
        text = value.strip()
        if text and not _looks_structured(text):
            return text
    return fallback


def _fallback_onboarding_question(snapshot: Mapping[str, Any]) -> str:
    coverage = snapshot.get("coverage", {})
    starting_fields = coverage.get("required_fields", {}) if isinstance(coverage, Mapping) else {}
    labels = {
        "genre": "genre",
        "tone": "tone",
        "setting": "setting",
        "player_role": "player role",
    }
    missing: list[str] = []
    if isinstance(starting_fields, Mapping):
        for key, is_done in starting_fields.items():
            if not is_done:
                missing.append(labels.get(str(key), str(key)))

    if not missing:
        return "I have what I need from the core story."
    if len(missing) == 1:
        return f"Tell me a little more about the story's {missing[0]}."
    if len(missing) == 2:
        return f"Tell me a little more about the story's {missing[0]} and {missing[1]}."
    return "Tell me a little more about the story's genre, tone, setting, or player role."


def _fallback_completion_message(snapshot: Mapping[str, Any], setup: Mapping[str, Any]) -> str:
    seed_scene = build_onboarding_seed_scene(setup)
    summary_text = str(seed_scene.get("summary_text") or "").strip()
    if summary_text and not _looks_structured(summary_text):
        return summary_text

    genre = _text(setup.get("genre"))
    setting = _text(setup.get("setting"))
    player_role = _text(setup.get("player_role"))
    if genre or setting or player_role:
        bits = ["Great, I have enough to start"]
        if genre:
            bits.append(f"a {genre} story")
        if setting:
            bits.append(f"set in {setting}")
        if player_role:
            bits.append(f"with you as {player_role}")
        return " ".join(bits) + "."

    return "Great, I have enough to start."


def get_onboarding_questions() -> list[OnboardingQuestion]:
    return list(_QUESTIONNAIRE)


def get_onboarding_question_order() -> list[str]:
    return [question.key for question in _QUESTIONNAIRE]


def build_onboarding_prompt_state(onboarding: Any) -> dict[str, Any]:
    answers = [
        {
            "index": answer.answer_index,
            "question": answer.question_text,
            "answer": answer.raw_answer_text,
            "normalized_answer": dict(answer.normalized_answer),
            "created_at": answer.created_at,
        }
        for answer in getattr(onboarding, "answers", [])
    ]
    current_setup = dict(getattr(onboarding, "normalized_setup", {}))
    coverage = {
        "required_fields": {field: bool(current_setup.get(field)) for field in REQUIRED_ONBOARDING_FIELDS},
        "starting_fields": {field: bool(current_setup.get(field)) for field in REQUIRED_ONBOARDING_FIELDS},
        "additional_fields": {field: bool(current_setup.get(field)) for field in _ADDITIONAL_FIELDS},
        "ready_to_start": all(current_setup.get(field) for field in REQUIRED_ONBOARDING_FIELDS),
    }
    return {
        "onboarding_id": getattr(onboarding, "id", None),
        "session_id": getattr(onboarding, "session_id", None),
        "status": getattr(onboarding, "status", None),
        "answer_count": len(answers),
        "normalized_setup": current_setup,
        "recent_answers": answers[-6:],
        "coverage": coverage,
        "generated_summary_text": getattr(onboarding, "generated_summary_text", ""),
        "seed_scene": dict(getattr(onboarding, "seed_scene", {})),
    }


def build_onboarding_system_prompt() -> str:
    return " ".join(
        [
            "You are the onboarding guide for Mind Game.",
            "Keep the conversation natural and ask at most one concise follow-up question at a time.",
            "Do not use a rigid survey or a fixed question order.",
            "Your goal is to gather only the required story fields: genre, tone, setting, and player role.",
            "The story cannot start until all four required fields are explicit.",
            "Do not ask for campaign goal, opening hook, or first event during onboarding.",
            "Ask again, more clearly, if the player does not explicitly provide the required field.",
            "Return JSON only as {\"content\":\"...\"} for questions or {\"updates\":{...}} for extraction.",
            "Keep the content short and friendly.",
        ],
    )


def build_onboarding_question_prompt(snapshot: Mapping[str, Any], *, missing_field: str, attempt_count: int = 0) -> str:
    payload = {
        "missing_field": missing_field,
        "attempt_count": attempt_count,
        "current_setup": snapshot.get("normalized_setup", {}),
        "coverage": snapshot.get("coverage", {}),
        "recent_answers": snapshot.get("recent_answers", []),
    }
    return f"Ask for the missing field as a narrator: {json.dumps(payload, sort_keys=True)}"


def build_onboarding_extraction_prompt(
    snapshot: Mapping[str, Any],
    *,
    asked_field: str,
    answer_text: str,
) -> str:
    payload = {
        "asked_field": asked_field,
        "answer_text": answer_text,
        "current_setup": snapshot.get("normalized_setup", {}),
        "coverage": snapshot.get("coverage", {}),
    }
    return f"Extract only explicit onboarding fields: {json.dumps(payload, sort_keys=True)}"


def should_complete_onboarding(snapshot: Mapping[str, Any]) -> bool:
    coverage = snapshot.get("coverage", {})
    if not isinstance(coverage, Mapping):
        return False
    starting_fields = coverage.get("required_fields", coverage.get("starting_fields", {}))
    if not isinstance(starting_fields, Mapping):
        return False
    return all(bool(starting_fields.get(field)) for field in REQUIRED_ONBOARDING_FIELDS)


def required_field_prompt(field: str, *, attempt_count: int = 0) -> str:
    prompts = {
        "genre": "What genre should this story be? For example: sci-fi adventure, fantasy, mystery, horror.",
        "tone": "What tone should it have? For example: mysterious, hopeful, grim, or humorous.",
        "setting": "Where does it begin? For example: a space station, a derelict spaceship, an alien planet.",
        "player_role": "Who is the player in this world? For example: scientist, explorer, pilot, investigator.",
    }
    reminder_prompts = {
        "genre": "I still need the genre in a short phrase, like sci-fi adventure or mystery.",
        "tone": "I still need the tone in a short phrase, like mysterious or hopeful.",
        "setting": "I still need the starting setting in a short phrase, like space station or derelict spaceship.",
        "player_role": "I still need the player role in a short phrase, like scientist or explorer.",
    }
    if attempt_count > 0:
        return reminder_prompts.get(field, f"I still need the {field}.")
    return prompts.get(field, f"What should the story's {field} be?")


def required_field_from_setup(snapshot: Mapping[str, Any]) -> str | None:
    current_setup = snapshot.get("normalized_setup", {})
    if not isinstance(current_setup, Mapping):
        return REQUIRED_ONBOARDING_FIELDS[0]
    for field in REQUIRED_ONBOARDING_FIELDS:
        if not _text(current_setup.get(field)):
            return field
    return None


def is_refusal_like(text: str) -> bool:
    normalized = text.strip().lower()
    phrases = (
        "you decide",
        "make it yourself",
        "surprise me",
        "up to you",
        "whatever",
        "don't know",
        "dont know",
        "idk",
        "no idea",
        "anything",
    )
    return any(phrase in normalized for phrase in phrases)


def extract_explicit_required_updates(answer_text: str) -> dict[str, Any]:
    text = answer_text.strip()
    if not text:
        return {}

    lowered = text.lower()
    updates: dict[str, Any] = {}

    genre_patterns = [
        (r"\b(sci[- ]?fi|science fiction|space opera|space adventure|cosmic adventure)\b", "sci-fi adventure"),
        (r"\bfantasy\b", "fantasy"),
        (r"\bmystery\b", "mystery"),
        (r"\bhorror\b", "horror"),
        (r"\bthriller\b", "thriller"),
        (r"\badventure\b", "adventure"),
    ]
    for pattern, value in genre_patterns:
        if re.search(pattern, lowered):
            updates["genre"] = value
            break

    tone_patterns = [
        (r"\bmysteri\w*\b", "mysterious"),
        (r"\bhopeful\b", "hopeful"),
        (r"\bgrim\b", "grim"),
        (r"\bhumor\w*\b|\bfunny\b", "humorous"),
        (r"\btense\b", "tense"),
        (r"\bdark\b", "dark"),
        (r"\binspir\w*\b", "inspiring"),
    ]
    for pattern, value in tone_patterns:
        if re.search(pattern, lowered):
            updates["tone"] = value
            break

    setting_patterns = [
        (r"\bderelict spaceship\b", "derelict spaceship"),
        (r"\bspace station\b", "space station"),
        (r"\borbital station\b", "orbital station"),
        (r"\bfar future\b", "the far future"),
        (r"\bend of the universe\b|\bedge of the universe\b", "the end of the universe"),
        (r"\balien planet\b", "an alien planet"),
        (r"\bdistant moon\b", "a distant moon"),
        (r"\bmoon\b", "a moon"),
        (r"\bplanet\b", "a planet"),
        (r"\buniverse\b", "the universe"),
        (r"\bspaceship\b", "a spaceship"),
        (r"\bship\b", "a ship"),
    ]
    for pattern, value in setting_patterns:
        if re.search(pattern, lowered):
            updates["setting"] = value
            break

    roles: list[str] = []
    if re.search(r"\bscientist\b", lowered):
        roles.append("scientist")
    if re.search(r"\bexplorer\b", lowered):
        roles.append("explorer")
    if re.search(r"\bpilot\b", lowered):
        roles.append("pilot")
    if re.search(r"\binvestigator\b|\bdetective\b", lowered):
        roles.append("investigator")
    if re.search(r"\bengineer\b", lowered):
        roles.append("engineer")
    if roles:
        updates["player_role"] = " ".join(dict.fromkeys(roles))

    return updates


class OllamaOnboardingReasoner:
    def __init__(self, model: Any, *, system_prompt: str | None = None) -> None:
        self.model = model
        self.system_prompt = system_prompt or build_onboarding_system_prompt()

    def decide(self, snapshot: Mapping[str, Any]) -> OnboardingDecision:
        missing_field = required_field_from_setup(snapshot)
        if missing_field is None:
            return OnboardingDecision(
                kind="complete",
                content=_fallback_completion_message(snapshot, snapshot.get("normalized_setup", {})),
                setup=dict(snapshot.get("normalized_setup", {})),
            )

        question = self.next_question(snapshot, missing_field=missing_field, attempt_count=_count_attempts(snapshot, missing_field))
        return OnboardingDecision(kind="question", content=question)

    def next_question(self, snapshot: Mapping[str, Any], *, missing_field: str, attempt_count: int = 0) -> str:
        prompt = build_onboarding_question_prompt(snapshot, missing_field=missing_field, attempt_count=attempt_count)
        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": prompt},
        ]
        accumulated = ""
        for chunk in self.model.stream(messages):
            accumulated += chunk.content or ""
        content = _strip_thinking_tags(accumulated)
        payload = self._extract_payload(content)
        if payload is not None:
            text = _coerce_message_text(payload.get("content"), required_field_prompt(missing_field, attempt_count=attempt_count))
            return text
        return _coerce_message_text(content, required_field_prompt(missing_field, attempt_count=attempt_count))

    def extract_updates(self, snapshot: Mapping[str, Any], *, answer_text: str, asked_field: str) -> dict[str, Any]:
        prompt = build_onboarding_extraction_prompt(snapshot, asked_field=asked_field, answer_text=answer_text)
        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": prompt},
        ]
        accumulated = ""
        for chunk in self.model.stream(messages):
            accumulated += chunk.content or ""
        content = _strip_thinking_tags(accumulated)
        updates = self._parse_updates(content, asked_field=asked_field, answer_text=answer_text)
        if updates:
            return updates
        return extract_explicit_required_updates(answer_text)

    def _parse_updates(self, content: str, *, asked_field: str, answer_text: str) -> dict[str, Any]:
        text = content.strip()
        payload = self._extract_payload(text)
        if payload is None:
            return {}

        updates = payload.get("updates") or payload.get("setup") or payload.get("normalized_setup") or payload
        if not isinstance(updates, Mapping):
            return {}
        return _normalize_required_updates(updates)

    def _extract_payload(self, text: str) -> dict[str, Any] | None:
        if not text:
            return None

        candidate = text.strip()
        if not candidate:
            return None

        if not candidate.startswith("{"):
            brace_index = candidate.find("{")
            if brace_index == -1:
                return None
            candidate = candidate[brace_index:]

        try:
            payload, _ = json.JSONDecoder().raw_decode(candidate)
        except json.JSONDecodeError:
            return None
        if not isinstance(payload, dict):
            return None
        return payload


def _normalize_required_updates(updates: Mapping[str, Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    for field in REQUIRED_ONBOARDING_FIELDS:
        value = _text(updates.get(field))
        if value:
            normalized[field] = value
    return normalized


def _count_attempts(snapshot: Mapping[str, Any], field: str) -> int:
    answers = snapshot.get("recent_answers", [])
    if not isinstance(answers, Sequence):
        return 0
    count = 0
    for answer in answers:
        if isinstance(answer, Mapping) and str(answer.get("question_key") or "") == field:
            count += 1
    return count


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
    campaign_goal = _text(normalized_setup.get("campaign_goal")) or _infer_campaign_goal(genre, tone, setting, player_role)
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
        "story_promises": story_promises or _infer_story_promises(genre, tone, setting, player_role, campaign_goal),
        "starting_state": starting_state,
        "memory_seed": memory_seed,
        "session_id": session_id,
        "onboarding_id": onboarding_id,
        "normalized_setup": dict(normalized_setup),
    }


def _infer_campaign_goal(
    genre: str | None,
    tone: str | None,
    setting: str | None,
    player_role: str | None,
) -> str:
    haystack = " ".join(part for part in [genre, tone, setting, player_role] if part).lower()
    if any(word in haystack for word in ("space", "sci-fi", "science fiction", "universe", "station", "planet", "moon", "orbit")):
        return "Uncover the truth behind the strange forces at the edge of the universe."
    if any(word in haystack for word in ("mystery", "investigator", "detective", "secret", "hidden")):
        return "Reveal the hidden truth and decide who can be trusted."
    if any(word in haystack for word in ("horror", "haunted", "dark", "grim")):
        return "Survive the threat and learn what is haunting this world."
    return "Find out what is really happening and what the player should do next."


def _infer_story_promises(
    genre: str | None,
    tone: str | None,
    setting: str | None,
    player_role: str | None,
    campaign_goal: str | None,
) -> list[str]:
    promises: list[str] = []
    if campaign_goal:
        promises.append(campaign_goal)
    if setting:
        promises.append(f"Explore the mystery of {setting}.")
    if genre:
        promises.append(f"Deliver a {genre} adventure.")
    if tone:
        promises.append(f"Keep the tone {tone}.")
    if player_role:
        promises.append(f"Let the player act as {player_role}.")
    if not promises:
        promises.append("Explore a compelling story together.")
    return promises[:4]
