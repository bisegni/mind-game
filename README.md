# mind-game

First step for an AI-evolving game prototype: a local Python chat loop powered by an OpenAI-compatible backend.

## Requirements

- Install and start `llama-server`, or another OpenAI-compatible backend such as Ollama
- Load or serve a chat model
- Install Python dependencies

## Install

```bash
python -m pip install -U pip
python -m pip install -e .
```

## Run

```bash
python -m mind_game.cli
```

Environment variables:

- `MIND_GAME_BASE_URL` selects the OpenAI-compatible backend; default is `http://127.0.0.1:8080`
- `OLLAMA_BASE_URL` and `OLLAMA_HOST` remain supported as fallback backend settings
- `MIND_GAME_MODEL`, `OPENAI_MODEL`, or `OLLAMA_MODEL` selects a model explicitly
- If no model is set, startup fetches available models from `{MIND_GAME_BASE_URL}/v1/models` and uses the first returned model id
- `OPENAI_API_KEY` is sent as a bearer token when fetching models or chat completions

## What it does

The CLI keeps a conversation going with the player and asks focused questions about the kind of game they want to build. That gives us a simple foundation for later game-evolution logic.
