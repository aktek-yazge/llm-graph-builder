"""Abstract base for language-specific code extractors."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from code_agent.graph.schema import (
    ClassNode,
    DecoratorNode,
    Edge,
    FunctionNode,
    ModuleNode,
    ParameterNode,
    VariableNode,
)


@dataclass
class ExtractionResult:
    """Aggregated output from parsing a single file."""

    module: ModuleNode | None = None
    classes: list[ClassNode] = field(default_factory=list)
    functions: list[FunctionNode] = field(default_factory=list)
    parameters: list[ParameterNode] = field(default_factory=list)
    variables: list[VariableNode] = field(default_factory=list)
    decorators: list[DecoratorNode] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    function_bodies: dict[str, str] = field(default_factory=dict)


class BaseExtractor(ABC):
    """Language-specific AST-to-graph extractor interface."""

    @abstractmethod
    def extract(self, source: bytes, file_path: str, module_qn: str) -> ExtractionResult:
        ...

    @abstractmethod
    def supported_extensions(self) -> list[str]:
        ...
