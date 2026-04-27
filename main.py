from __future__ import annotations

import argparse
import os
from urllib.parse import urlparse, urlunparse

from mind_game.cli import OpenAICompatibleChatClient, fetch_openai_available_models, normalize_openai_base_url


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Mind Game starter")
    parser.add_argument("--model", default=None, help="Ollama/OpenAI-compatible model name to use")
    parser.add_argument(
        "--base-url",
        default=resolve_base_url(),
        help="OpenAI-compatible backend URL",
    )
    return parser.parse_args()


def resolve_base_url() -> str:
    configured = os.environ.get("MIND_GAME_BASE_URL")
    if configured:
        return configured

    host = os.environ.get("OLLAMA_HOST")
    if host:
        return normalize_ollama_host(host)

    return os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:8080")


def normalize_ollama_host(host: str) -> str:
    parsed = urlparse(host if "://" in host else f"http://{host}")
    netloc = parsed.netloc

    if ":" not in netloc:
        netloc = f"{netloc}:11434"

    return urlunparse((parsed.scheme or "http", netloc, parsed.path, parsed.params, parsed.query, parsed.fragment))


def resolve_model_name(base_url: str, selected_model: str | None = None) -> str:
    if selected_model:
        return selected_model

    configured_model = os.environ.get("MIND_GAME_MODEL") or os.environ.get("OPENAI_MODEL") or os.environ.get("OLLAMA_MODEL")
    if configured_model:
        return configured_model

    models = fetch_openai_available_models(base_url)
    if not models:
        raise RuntimeError(f"No models returned by {openai_models_endpoint(base_url)}")
    return models[0]


def openai_models_endpoint(base_url: str) -> str:
    return f"{normalize_openai_base_url(base_url).rstrip('/')}/models"


def build_system_prompt() -> str:
    return " ".join(
        [
            "You are the opening conversation loop for an AI-evolving game prototype.",
            "Ask one concise question at a time.",
            "Learn what kind of game the user wants to build.",
            "Keep responses short and useful.",
        ]
    )


def main() -> int:
    args = parse_args()
    try:
        model_name = resolve_model_name(args.base_url, args.model)
    except RuntimeError as error:
        print(f"Model selection failed: {error}")
        return 1

    model = OpenAICompatibleChatClient(model=model_name, base_url=args.base_url, temperature=0.7)

    messages = [{"role": "system", "content": build_system_prompt()}]
    print(f'Mind Game chat loop ready using OpenAI-compatible backend model "{model_name}" at {args.base_url}.')
    print('Type "exit" to quit.\n')

    try:
        while True:
            user_text = input("You > ").strip()
            if user_text.lower() in {"exit", "quit", "bye"}:
                break
            if not user_text:
                continue

            messages.append({"role": "user", "content": user_text})
            print("AI  > ", end="", flush=True)

            reply_parts: list[str] = []
            for chunk in model.stream(messages):
                text = chunk.content
                if not text:
                    continue
                reply_parts.append(text)
                print(text, end="", flush=True)

            reply_text = "".join(reply_parts).strip()
            messages.append({"role": "assistant", "content": reply_text})
            print("\n")
    except (KeyboardInterrupt, EOFError):
        print("\nGoodbye.")
    except Exception as error:  # pragma: no cover - CLI guard
        print(f"Chat loop failed: {error}")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
