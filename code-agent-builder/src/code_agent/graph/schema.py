"""Kuzu graph schema definitions for code representation."""

from dataclasses import dataclass, field

NODE_TABLES: list[str] = [
    """CREATE NODE TABLE IF NOT EXISTS Module(
        path STRING,
        name STRING,
        language STRING,
        file_hash STRING,
        line_count INT64,
        last_analyzed_at STRING,
        summary STRING,
        PRIMARY KEY(path)
    )""",
    """CREATE NODE TABLE IF NOT EXISTS Class(
        qualified_name STRING,
        name STRING,
        docstring STRING,
        start_line INT64,
        end_line INT64,
        is_abstract BOOLEAN,
        complexity INT64,
        design_pattern STRING,
        PRIMARY KEY(qualified_name)
    )""",
    """CREATE NODE TABLE IF NOT EXISTS Function(
        qualified_name STRING,
        name STRING,
        signature STRING,
        docstring STRING,
        body_hash STRING,
        start_line INT64,
        end_line INT64,
        complexity INT64,
        is_async BOOLEAN,
        param_count INT64,
        purpose STRING,
        category STRING,
        PRIMARY KEY(qualified_name)
    )""",
    """CREATE NODE TABLE IF NOT EXISTS Parameter(
        qualified_name STRING,
        name STRING,
        type_hint STRING,
        default_value STRING,
        position INT64,
        PRIMARY KEY(qualified_name)
    )""",
    """CREATE NODE TABLE IF NOT EXISTS Variable(
        qualified_name STRING,
        name STRING,
        type_hint STRING,
        scope STRING,
        is_class_field BOOLEAN,
        PRIMARY KEY(qualified_name)
    )""",
    """CREATE NODE TABLE IF NOT EXISTS Decorator(
        qualified_name STRING,
        name STRING,
        has_args BOOLEAN,
        PRIMARY KEY(qualified_name)
    )""",
]

REL_TABLES: list[str] = [
    "CREATE REL TABLE IF NOT EXISTS CONTAINS(FROM Module TO Class, FROM Module TO Function, FROM Module TO Variable)",
    "CREATE REL TABLE IF NOT EXISTS HAS_METHOD(FROM Class TO Function)",
    "CREATE REL TABLE IF NOT EXISTS HAS_PROPERTY(FROM Class TO Variable)",
    "CREATE REL TABLE IF NOT EXISTS INHERITS(FROM Class TO Class)",
    "CREATE REL TABLE IF NOT EXISTS CALLS(FROM Function TO Function)",
    "CREATE REL TABLE IF NOT EXISTS IMPORTS(FROM Module TO Module, imported_name STRING, alias STRING)",
    "CREATE REL TABLE IF NOT EXISTS HAS_PARAMETER(FROM Function TO Parameter)",
    "CREATE REL TABLE IF NOT EXISTS RETURNS_TYPE(FROM Function TO Class, return_type STRING)",
    "CREATE REL TABLE IF NOT EXISTS USES(FROM Function TO Function, FROM Function TO Class, FROM Function TO Variable)",
    "CREATE REL TABLE IF NOT EXISTS DECORATED_BY(FROM Function TO Decorator, FROM Class TO Decorator)",
]


@dataclass
class ModuleNode:
    path: str
    name: str
    language: str
    file_hash: str
    line_count: int
    summary: str = ""


@dataclass
class ClassNode:
    qualified_name: str
    name: str
    docstring: str = ""
    start_line: int = 0
    end_line: int = 0
    is_abstract: bool = False
    complexity: int = 0
    design_pattern: str = ""


@dataclass
class FunctionNode:
    qualified_name: str
    name: str
    signature: str = ""
    docstring: str = ""
    body_hash: str = ""
    start_line: int = 0
    end_line: int = 0
    complexity: int = 0
    is_async: bool = False
    param_count: int = 0
    purpose: str = ""
    category: str = ""


@dataclass
class ParameterNode:
    qualified_name: str
    name: str
    type_hint: str = ""
    default_value: str = ""
    position: int = 0


@dataclass
class VariableNode:
    qualified_name: str
    name: str
    type_hint: str = ""
    scope: str = ""
    is_class_field: bool = False


@dataclass
class DecoratorNode:
    qualified_name: str
    name: str
    has_args: bool = False


@dataclass
class Edge:
    rel_type: str
    from_key: str
    to_key: str
    properties: dict = field(default_factory=dict)
    from_table: str = ""
    to_table: str = ""
