from __future__ import annotations

import os

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_ollama import ChatOllama

from .prompt import build_system_prompt, is_exit_command


def main() -> int:
    model_name = os.environ.get("OLLAMA_MODEL", "llama3.1")
    base_url = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434")

    model = ChatOllama(
        model=model_name,
        base_url=base_url,
        temperature=0.7,
        max_retries=2,
    )

    messages = [SystemMessage(content=build_system_prompt())]

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

            messages.append(HumanMessage(content=trimmed))

            response = model.invoke(messages)
            reply_text = response.content if isinstance(response.content, str) else str(response.content)

            messages.append(AIMessage(content=reply_text))
            print(f"AI  > {reply_text}\n")
    except (KeyboardInterrupt, EOFError):
        print("\nGoodbye.")
    except Exception as error:  # pragma: no cover - defensive CLI guard
        print(f"Chat loop failed: {error}")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
