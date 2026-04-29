"""quality_gate — Conditional pass/fail based on confidence + label policy.

Two routing modes:

1. **numeric_only** (legacy default for backward compat):
   - Sadece ``avg_confidence >= threshold`` kontrolü.
   - Output: pass / fail (eski davranış).

2. **label_aware** (graphify-adopted, önerilen):
   - AMBIGUOUS sayısı ``max_ambiguous`` eşiğini aşıyorsa → ambiguous port'tan
     human_review'a yönlenir (pass/fail bağımsız).
   - Aksi takdirde numeric ortalama threshold ile pass/fail kararı.
   - Üç output: pass, fail, ambiguous. Workflow tasarımcısı ambiguous port'u
     bir ``human_review`` node'una bağlar; bağlamazsa AMBIGUOUS olanlar
     drop edilir (uyarı log'u atılır).

Input ``in`` dict olarak gelir; ``avg_confidence`` ve ``ambiguous_count`` /
``label_counts`` field'larına bakar (entity_extractor bunları döndürür).
"""
from __future__ import annotations

import logging
from typing import Any

from ..node_registry import NodeType, ExecutionContext, PortDef, register
from ..models import PortDirection

logger = logging.getLogger(__name__)


@register
class QualityGateNode(NodeType):
    type_id = "quality_gate"
    label = "Kalite Kapisi"
    description = (
        "Guven esigi + AMBIGUOUS label politikasina gore pass/fail/ambiguous"
        " yonlendirir."
    )
    category = "control"
    icon = "shield"
    color = "#E53E3E"

    @classmethod
    def params_schema(cls) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "threshold": {
                    "type": "number",
                    "default": 0.7,
                    "description": "Minimum ortalama guven esigi (0..1)",
                },
                "policy": {
                    "type": "string",
                    "enum": ["numeric_only", "label_aware"],
                    "default": "label_aware",
                    "description": (
                        "numeric_only = sadece avg_confidence; "
                        "label_aware = AMBIGUOUS sayisini ayri port'a yonlendirir"
                    ),
                },
                "max_ambiguous": {
                    "type": "integer",
                    "default": 0,
                    "description": (
                        "label_aware: bu sayidan fazla AMBIGUOUS varsa veri"
                        " 'ambiguous' port'una yonlendirilir (0=hicbirine tolerans)"
                    ),
                },
            },
        }

    @classmethod
    def input_ports(cls) -> list[PortDef]:
        return [PortDef(name="in", direction=PortDirection.INPUT, data_type="any")]

    @classmethod
    def output_ports(cls) -> list[PortDef]:
        return [
            PortDef(name="pass", direction=PortDirection.OUTPUT, data_type="any"),
            PortDef(name="fail", direction=PortDirection.OUTPUT, data_type="any"),
            PortDef(name="ambiguous", direction=PortDirection.OUTPUT, data_type="any"),
        ]

    async def execute(
        self,
        ctx: ExecutionContext,
        params: dict[str, Any],
        inputs: dict[str, Any],
    ) -> dict[str, Any]:
        threshold = float(params.get("threshold", 0.7))
        policy = str(params.get("policy", "label_aware"))
        max_ambiguous = int(params.get("max_ambiguous", 0))
        data = inputs.get("in", {})

        avg_confidence = 0.0
        ambiguous_count = 0
        if isinstance(data, dict):
            avg_confidence = float(data.get("avg_confidence", 0.0))
            # entity_extractor explicit count döndürür; yoksa label_counts'tan oku
            ambiguous_count = int(
                data.get("ambiguous_count")
                or (data.get("label_counts") or {}).get("AMBIGUOUS", 0)
            )
        elif isinstance(data, list):
            scores = [float(item.get("confidence", 0)) for item in data if isinstance(item, dict)]
            avg_confidence = sum(scores) / len(scores) if scores else 0.0
            ambiguous_count = sum(
                1
                for item in data
                if isinstance(item, dict)
                and str(item.get("confidence_label", "")).upper() == "AMBIGUOUS"
            )

        # Label-aware: önce AMBIGUOUS yönlendirmesi
        if policy == "label_aware" and ambiguous_count > max_ambiguous:
            logger.info(
                "quality_gate: %d AMBIGUOUS (max=%d) -> 'ambiguous' port'una yonlendirildi",
                ambiguous_count, max_ambiguous,
            )
            return {
                "pass": None,
                "fail": None,
                "ambiguous": data,
                "_gate_result": "ambiguous",
                "_avg_confidence": avg_confidence,
                "_ambiguous_count": ambiguous_count,
            }

        # Numeric karar (legacy davranış aynen)
        passed = avg_confidence >= threshold
        gate_result = "pass" if passed else "fail"
        logger.info(
            "quality_gate: policy=%s avg=%.3f threshold=%.3f ambiguous=%d -> %s",
            policy, avg_confidence, threshold, ambiguous_count, gate_result,
        )
        return {
            "pass": data if passed else None,
            "fail": data if not passed else None,
            "ambiguous": None,
            "_gate_result": gate_result,
            "_avg_confidence": avg_confidence,
            "_ambiguous_count": ambiguous_count,
        }
