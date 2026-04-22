from __future__ import annotations

import argparse
import os
from urllib.parse import urlparse, urlunparse


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Mind Game starter")
    parser.add_argument("--model", default="llama3.1", help="Ollama model name to use")
    parser.add_argument(
        "--base-url",
        default=resolve_base_url(),
        help="Ollama server URL",
    )
    return parser.parse_args()


def resolve_base_url() -> str:
    host = os.environ.get("OLLAMA_HOST")
    if host:
        return normalize_ollama_host(host)

    return os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434")


def normalize_ollama_host(host: str) -> str:
    parsed = urlparse(host if "://" in host else f"http://{host}")
    netloc = parsed.netloc

    if ":" not in netloc:
        netloc = f"{netloc}:11434"

    return urlunparse((parsed.scheme or "http", netloc, parsed.path, parsed.params, parsed.query, parsed.fragment))


def chunk_text(chunk: object) -> str:
    content = getattr(chunk, "content", "")
    return content if isinstance(content, str) else str(content or "")


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
        from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
        from langchain_ollama import ChatOllama
    except ModuleNotFoundError as error:  # pragma: no cover - depends on local install
        print(f"Missing dependency: {error.name}. Install with `python -m pip install -e .`.")
        return 1

    model = ChatOllama(model=args.model, base_url=args.base_url, temperature=0.7, max_retries=2)

    messages = [SystemMessage(content=build_system_prompt())]
    print(f'Mind Game chat loop ready using Ollama model "{args.model}" at {args.base_url}.')
    print('Type "exit" to quit.\n')

    try:
        while True:
            user_text = input("You > ").strip()
            if user_text.lower() in {"exit", "quit", "bye"}:
                break
            if not user_text:
                continue

            messages.append(HumanMessage(content=user_text))
            print("AI  > ", end="", flush=True)

            reply_parts: list[str] = []
            for chunk in model.stream(messages):
                text = chunk_text(chunk)
                if not text:
                    continue
                reply_parts.append(text)
                print(text, end="", flush=True)

            reply_text = "".join(reply_parts).strip()
            messages.append(AIMessage(content=reply_text))
            print("\n")
    except (KeyboardInterrupt, EOFError):
        print("\nGoodbye.")
    except Exception as error:  # pragma: no cover - CLI guard
        print(f"Chat loop failed: {error}")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
