import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from mind_game.engine import ReActDecision

import mind_game.cli as cli


class FakeReasoner:
    def decide(self, snapshot, tools):
        return ReActDecision(kind="final", content=f"echo:{snapshot['player_input']}")


class CliTests(unittest.TestCase):
    def test_main_uses_package_cli_and_engine_loop(self) -> None:
        with patch.dict("os.environ", {"OLLAMA_MODEL": "test-model", "OLLAMA_BASE_URL": "http://example.local:11434"}, clear=True):
            with patch.object(cli, "build_reasoner", return_value=FakeReasoner()) as build_reasoner:
                with patch("builtins.input", side_effect=["hello", "exit"]):
                    stdout = io.StringIO()
                    with redirect_stdout(stdout):
                        exit_code = cli.main()

        output = stdout.getvalue()

        self.assertEqual(exit_code, 0)
        self.assertIn('Mind Game chat loop ready using Ollama model "test-model" at http://example.local:11434.', output)
        self.assertIn("AI  > echo:hello", output)
        build_reasoner.assert_called_once_with("test-model", "http://example.local:11434")


if __name__ == "__main__":
    unittest.main()
