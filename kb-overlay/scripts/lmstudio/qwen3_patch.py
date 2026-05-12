"""Qwen3.6 thinking-mode patcher.

Qwen3 family auto-thinks regardless of API params. Workaround: prefill assistant
message with empty `<think></think>` block. Patches OpenAIProvider.complete_json.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# Ensure kb-overlay src on path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kb_overlay.relations.extractor import OpenAIProvider

_PREFILL = "<think>\n\n</think>\n\n"


def _patched_complete_json(
    self,
    system_prompt: str,
    user_prompt: str,
    *,
    json_schema=None,
    temperature: float = 0.0,
):
    kwargs = {
        "model": self.model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
            {"role": "assistant", "content": _PREFILL},
        ],
        "temperature": temperature,
    }
    if json_schema is not None:
        kwargs["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                "name": "EntityExtractionResponse",
                "schema": json_schema,
                "strict": True,
            },
        }
    else:
        kwargs["response_format"] = {"type": "json_object"}

    resp = self.client.chat.completions.create(**kwargs)
    content = resp.choices[0].message.content or "{}"
    content = content.strip()
    if content.startswith("```"):
        lines = content.split("\n")
        content = "\n".join(lines[1:-1] if lines[-1].startswith("```") else lines[1:])
    return json.loads(content)


def install():
    OpenAIProvider.complete_json = _patched_complete_json
    print("[patch] OpenAIProvider.complete_json with Qwen3 think-prefill", file=sys.stderr)


if __name__ == "__main__":
    install()
    from click.testing import CliRunner
    from kb_overlay.cli.main import cli

    runner = CliRunner()
    result = runner.invoke(cli, sys.argv[1:], standalone_mode=False)
    if result.exception:
        import traceback
        traceback.print_exception(type(result.exception), result.exception, result.exception.__traceback__)
        sys.exit(1)
    print(result.output)
