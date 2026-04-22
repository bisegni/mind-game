# mind-game

First step for an AI-evolving game prototype: a local Python LangChain chat loop powered by Ollama.

## Requirements

- Install Ollama
- Pull a chat model, for example `ollama pull llama3.1`
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

- `OLLAMA_MODEL` defaults to `llama3.1`
- `OLLAMA_BASE_URL` defaults to `http://127.0.0.1:11434`

## What it does

The CLI keeps a conversation going with the player and asks focused questions about the kind of game they want to build. That gives us a simple foundation for later game-evolution logic.
