import unittest
from unittest.mock import patch

import main
import mind_game.cli as cli


class MainTests(unittest.TestCase):
    def test_parse_args_reads_model_argument(self) -> None:
        with patch("sys.argv", ["main.py", "--model", "gpt-oss:120b"]):
            args = main.parse_args()

        self.assertEqual(args.model, "gpt-oss:120b")

    def test_parse_args_leaves_model_unselected_by_default(self) -> None:
        with patch("sys.argv", ["main.py"]):
            args = main.parse_args()

        self.assertIsNone(args.model)

    def test_parse_args_reads_base_url_argument(self) -> None:
        with patch("sys.argv", ["main.py", "--base-url", "http://localhost:11434"]):
            args = main.parse_args()

        self.assertEqual(args.base_url, "http://localhost:11434")

    def test_resolve_base_url_prefers_ollama_host(self) -> None:
        with patch.dict("os.environ", {"OLLAMA_HOST": "remote.example.com:11434"}, clear=True):
            self.assertEqual(main.resolve_base_url(), "http://remote.example.com:11434")

    def test_resolve_base_url_prefers_mind_game_base_url(self) -> None:
        with patch.dict("os.environ", {"MIND_GAME_BASE_URL": "http://localhost:8080"}, clear=True):
            self.assertEqual(main.resolve_base_url(), "http://localhost:8080")

    def test_resolve_base_url_defaults_to_llama_server(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(main.resolve_base_url(), "http://127.0.0.1:8080")

    def test_resolve_base_url_uses_ollama_host_url_as_is(self) -> None:
        with patch.dict("os.environ", {"OLLAMA_HOST": "https://remote.example.com:11434"}, clear=True):
            self.assertEqual(main.resolve_base_url(), "https://remote.example.com:11434")

    def test_resolve_base_url_adds_default_port_when_missing(self) -> None:
        with patch.dict("os.environ", {"OLLAMA_HOST": "slacstudio.local"}, clear=True):
            self.assertEqual(main.resolve_base_url(), "http://slacstudio.local:11434")

    def test_resolve_base_url_adds_default_port_to_schemed_host(self) -> None:
        with patch.dict("os.environ", {"OLLAMA_HOST": "http://slacstudio.local"}, clear=True):
            self.assertEqual(main.resolve_base_url(), "http://slacstudio.local:11434")

    def test_resolve_model_name_prefers_selected_argument(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            with patch.object(main, "fetch_openai_available_models") as fetch_models:
                model_name = main.resolve_model_name("http://example.local:11434", "chosen-model")

        self.assertEqual(model_name, "chosen-model")
        fetch_models.assert_not_called()

    def test_resolve_model_name_fetches_first_openai_model_when_unset(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            with patch.object(main, "fetch_openai_available_models", return_value=["gpt-4.1-mini", "gpt-4.1"]):
                model_name = main.resolve_model_name("https://api.openai.com")

        self.assertEqual(model_name, "gpt-4.1-mini")

    def test_fetch_openai_available_models_reads_model_ids(self) -> None:
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                return False

            def read(self):
                return b'{"data":[{"id":"gpt-4.1-mini"},{"id":"gpt-4.1"}]}'

        with patch.dict("os.environ", {"OPENAI_API_KEY": "secret"}, clear=True):
            with patch.object(cli, "urlopen", return_value=FakeResponse()) as urlopen:
                models = main.fetch_openai_available_models("https://api.openai.com")

        self.assertEqual(models, ["gpt-4.1-mini", "gpt-4.1"])
        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "https://api.openai.com/v1/models")
        self.assertEqual(request.headers["Authorization"], "Bearer secret")


if __name__ == "__main__":
    unittest.main()
