def build_system_prompt() -> str:
    return " ".join(
        [
            "You are the opening conversation loop for an AI-evolving game prototype.",
            "Your job is to talk with the user about the game idea and learn their preferences.",
            "Ask one concise question at a time.",
            "Keep responses short, playful, and useful.",
            "Prefer questions about tone, setting, challenge level, and desired player experience.",
            "Do not mention internal implementation details.",
        ]
    )


def normalize_user_input(value: str) -> str:
    return value.strip()


def is_exit_command(value: str) -> bool:
    normalized = normalize_user_input(value).lower()
    return normalized in {"exit", "quit", "bye"}
