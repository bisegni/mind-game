#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ ! -f "$SCRIPT_DIR/.env" ]]; then
  echo "ERROR: .env not found. Copy .env.example to .env and fill in OPENAI_API_KEY."
  exit 1
fi

set -a
# shellcheck source=.env
source "$SCRIPT_DIR/.env"
set +a

if [[ -z "${OPENAI_API_KEY:-}" ]]; then
  echo "ERROR: OPENAI_API_KEY not set in .env"
  exit 1
fi

export OPENAI_API_KEY
export MIND_GAME_BASE_URL="${MIND_GAME_BASE_URL:-https://api.openai.com/v1}"
export MIND_GAME_MODEL="${MIND_GAME_MODEL:-gpt-4o}"

exec python -m mind_game.cli "$@"
