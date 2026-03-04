"""LLM-based semantic enrichment for code graph nodes (Phase 2)."""

from __future__ import annotations

import json
import logging
import os
import threading
from typing import Any

from code_agent.graph.kuzu_client import KuzuClient

logger = logging.getLogger(__name__)

ENRICHMENT_PROMPT = """Analyze the following code and respond with ONLY a JSON object (no markdown, no explanation):

File: {file_path}
Language: {language}

Code:
```
{code}
```

For each function/method in this code, provide:
- "purpose": One-sentence description of what it does
- "category": One of: handler, utility, validator, model, repository, service, middleware, test, config, factory, decorator_fn, cli

For the module itself:
- "summary": One-sentence summary of the module's purpose

Response format:
{{
  "module_summary": "...",
  "functions": {{
    "function_name": {{"purpose": "...", "category": "..."}},
    ...
  }},
  "classes": {{
    "class_name": {{"design_pattern": "..."}},
    ...
  }}
}}"""


class LLMEnricher:
    """Enriches code graph nodes with LLM-generated semantic metadata."""

    def __init__(self, graph: KuzuClient, model: str = "gemini-2.0-flash"):
        self.graph = graph
        self.model = model
        self._client = None

    def _get_client(self):
        if self._client is None:
            api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
            if not api_key:
                logger.warning("No GEMINI_API_KEY or GOOGLE_API_KEY found, LLM enrichment disabled")
                return None
            from google import genai
            self._client = genai.Client(api_key=api_key)
        return self._client

    def enrich_module(self, module_path: str, source_code: str, language: str) -> dict[str, Any] | None:
        client = self._get_client()
        if not client:
            return None

        prompt = ENRICHMENT_PROMPT.format(
            file_path=module_path,
            language=language,
            code=source_code[:8000],
        )

        try:
            response = client.models.generate_content(
                model=self.model,
                contents=prompt,
            )
            text = response.text.strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[1].rsplit("```", 1)[0]
            data = json.loads(text)
            self._apply_enrichment(module_path, data)
            return data
        except Exception as e:
            logger.error("LLM enrichment failed for %s: %s", module_path, e)
            return None

    def enrich_project_async(self, modules: list[dict]) -> None:
        """Run enrichment in a background thread."""
        thread = threading.Thread(
            target=self._enrich_batch,
            args=(modules,),
            daemon=True,
        )
        thread.start()
        logger.info("LLM enrichment started in background for %d modules", len(modules))

    def _enrich_batch(self, modules: list[dict]) -> None:
        for mod in modules:
            path = mod.get("path", "")
            try:
                from pathlib import Path
                source = Path(path).read_text(errors="replace")
                language = mod.get("language", "python")
                self.enrich_module(path, source, language)
            except Exception as e:
                logger.error("Enrichment error for %s: %s", path, e)

    def _apply_enrichment(self, module_path: str, data: dict) -> None:
        summary = data.get("module_summary", "")
        if summary:
            self.graph.execute(
                "MATCH (m:Module {path: $path}) SET m.summary = $summary",
                {"path": module_path, "summary": summary},
            )

        functions = data.get("functions", {})
        for fn_name, meta in functions.items():
            purpose = meta.get("purpose", "")
            category = meta.get("category", "")
            if purpose or category:
                self.graph.execute(
                    "MATCH (f:Function) WHERE f.name = $name AND f.qualified_name CONTAINS $module "
                    "SET f.purpose = $purpose, f.category = $category",
                    {"name": fn_name, "module": module_path, "purpose": purpose, "category": category},
                )

        classes = data.get("classes", {})
        for cls_name, meta in classes.items():
            pattern = meta.get("design_pattern", "")
            if pattern:
                self.graph.execute(
                    "MATCH (c:Class) WHERE c.name = $name AND c.qualified_name CONTAINS $module "
                    "SET c.design_pattern = $pattern",
                    {"name": cls_name, "module": module_path, "pattern": pattern},
                )
