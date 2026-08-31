import json
import unittest
from pathlib import Path
from unittest.mock import patch

from layout_configurator.brief import parse_text_brief
from layout_configurator.llm import parse_with_openai_compatible


class _Response:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _limit):
        return self.payload


class LlmTests(unittest.TestCase):
    def test_provider_response_is_schema_checked_before_layout_use(self):
        canonical = parse_text_brief(Path("examples/brief.txt").read_text(encoding="utf-8")).to_dict()
        response = {"choices": [{"message": {"content": "```json\n" + json.dumps(canonical) + "\n```"}}]}

        with patch("layout_configurator.llm.urlopen", return_value=_Response(response)) as opened:
            spec = parse_with_openai_compatible(
                "brief",
                endpoint="https://provider.test/v1/chat/completions",
                model="test-model",
                api_key="secret",
            )

        self.assertEqual(spec.project_name, "Дом из текстового ТЗ")
        request = opened.call_args.args[0]
        self.assertEqual(request.headers["Authorization"], "Bearer secret")
        self.assertIn("JSON Schema", json.loads(request.data.decode("utf-8"))["messages"][1]["content"])

    def test_provider_coordinates_are_rejected_by_schema(self):
        canonical = parse_text_brief(Path("examples/brief.txt").read_text(encoding="utf-8")).to_dict()
        canonical["rooms"][0]["x"] = 0
        response = {"choices": [{"message": {"content": json.dumps(canonical)}}]}

        with patch("layout_configurator.llm.urlopen", return_value=_Response(response)):
            with self.assertRaises(ValueError):
                parse_with_openai_compatible(
                    "brief",
                    endpoint="https://provider.test/v1/chat/completions",
                    model="test-model",
                    api_key="secret",
                )


if __name__ == "__main__":
    unittest.main()
