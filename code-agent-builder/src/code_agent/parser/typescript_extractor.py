"""Rule-based TypeScript/TSX AST extractor using tree-sitter."""

from __future__ import annotations

import hashlib
from tree_sitter import Node

from code_agent.graph.schema import (
    ClassNode,
    DecoratorNode,
    Edge,
    FunctionNode,
    ParameterNode,
    VariableNode,
)

from .base import BaseExtractor, ExtractionResult


class TypeScriptExtractor(BaseExtractor):

    def supported_extensions(self) -> list[str]:
        return [".ts", ".tsx"]

    def extract(self, source: bytes, file_path: str, module_qn: str) -> ExtractionResult:
        from tree_sitter import Parser, Language
        import tree_sitter_typescript as tslib

        ext = file_path.rsplit(".", 1)[-1]
        lang_fn = tslib.language_tsx if ext == "tsx" else tslib.language_typescript
        lang = Language(lang_fn())
        parser = Parser(lang)
        tree = parser.parse(source)

        self._source = source
        self._file_path = file_path
        self._module_qn = module_qn
        self._result = ExtractionResult()

        self._walk_module(tree.root_node)
        return self._result

    # ── Module-level ──────────────────────────────────────────

    def _walk_module(self, root: Node) -> None:
        for child in root.children:
            self._visit_top_level(child)

    def _visit_top_level(self, node: Node) -> None:
        if node.type == "function_declaration":
            self._extract_function(node, self._module_qn)
            self._result.edges.append(Edge("CONTAINS", self._file_path, self._fn_qn(node, self._module_qn)))

        elif node.type == "class_declaration":
            self._extract_class(node, self._module_qn)
            self._result.edges.append(Edge("CONTAINS", self._file_path, self._cls_qn(node, self._module_qn)))

        elif node.type == "export_statement":
            for child in node.children:
                self._visit_top_level(child)

        elif node.type in ("lexical_declaration", "variable_declaration"):
            self._extract_variable_declaration(node, self._module_qn)

        elif node.type in ("import_statement",):
            self._extract_import(node)

        elif node.type == "expression_statement":
            if node.children and node.children[0].type == "assignment_expression":
                pass

    # ── Class ─────────────────────────────────────────────────

    def _extract_class(self, node: Node, parent_qn: str) -> None:
        name = self._node_text(node.child_by_field_name("name"))
        cls_qn = f"{parent_qn}.{name}"

        heritage = node.child_by_field_name("heritage") or self._find_child_type(node, "class_heritage")
        if heritage:
            for child in self._walk(heritage):
                if child.type == "extends_clause":
                    for type_node in child.children:
                        if type_node.type in ("identifier", "member_expression"):
                            base_name = self._node_text(type_node)
                            if base_name and base_name != "extends":
                                self._result.edges.append(Edge("INHERITS", cls_qn, base_name))

        body = node.child_by_field_name("body")
        cls_node = ClassNode(
            qualified_name=cls_qn,
            name=name,
            docstring=self._extract_jsdoc(node),
            start_line=node.start_point[0] + 1,
            end_line=node.end_point[0] + 1,
            is_abstract=self._has_modifier(node, "abstract"),
        )
        self._result.classes.append(cls_node)

        if body:
            self._walk_class_body(body, cls_qn)

    def _walk_class_body(self, body: Node, cls_qn: str) -> None:
        for child in body.children:
            if child.type == "method_definition":
                self._extract_function(child, cls_qn, is_method=True)
                self._result.edges.append(Edge("HAS_METHOD", cls_qn, self._fn_qn(child, cls_qn)))
            elif child.type in ("public_field_definition", "property_declaration"):
                self._extract_class_field(child, cls_qn)

    # ── Function ──────────────────────────────────────────────

    def _extract_function(self, node: Node, parent_qn: str, is_method: bool = False) -> None:
        name = self._node_text(node.child_by_field_name("name"))
        if not name:
            return
        fn_qn = f"{parent_qn}.{name}"

        params_node = node.child_by_field_name("parameters")
        params = self._extract_parameters(params_node, fn_qn)

        return_type_node = node.child_by_field_name("return_type")
        return_type = self._node_text(return_type_node) if return_type_node else ""
        if return_type.startswith(":"):
            return_type = return_type[1:].strip()

        body = node.child_by_field_name("body")
        body_text = self._node_text(body) if body else ""
        body_hash = hashlib.md5(body_text.encode()).hexdigest()

        is_async = self._has_modifier(node, "async")

        fn_node = FunctionNode(
            qualified_name=fn_qn,
            name=name,
            signature=f"{'async ' if is_async else ''}function {name}({self._node_text(params_node) if params_node else ''}){': ' + return_type if return_type else ''}",
            docstring=self._extract_jsdoc(node),
            body_hash=body_hash,
            start_line=node.start_point[0] + 1,
            end_line=node.end_point[0] + 1,
            complexity=self._count_branches(body) if body else 0,
            is_async=is_async,
            param_count=len(params),
        )
        self._result.functions.append(fn_node)
        self._result.function_bodies[fn_qn] = body_text

        if return_type:
            self._result.edges.append(Edge("RETURNS_TYPE", fn_qn, return_type, {"return_type": return_type}))

        if body:
            self._extract_calls(body, fn_qn)

    # ── Parameters ────────────────────────────────────────────

    def _extract_parameters(self, params_node: Node | None, fn_qn: str) -> list[ParameterNode]:
        if not params_node:
            return []

        params: list[ParameterNode] = []
        position = 0

        for child in params_node.children:
            if child.type not in ("required_parameter", "optional_parameter", "rest_parameter",
                                   "identifier", "assignment_pattern"):
                continue

            name = ""
            type_hint = ""
            default = ""

            if child.type == "identifier":
                name = self._node_text(child)
            elif child.type in ("required_parameter", "optional_parameter"):
                pattern = child.child_by_field_name("pattern")
                name = self._node_text(pattern) if pattern else self._node_text(child.children[0])
                type_ann = child.child_by_field_name("type")
                type_hint = self._node_text(type_ann).lstrip(":").strip() if type_ann else ""
                val = child.child_by_field_name("value")
                default = self._node_text(val) if val else ""
            elif child.type == "rest_parameter":
                name = "..." + self._node_text(child.children[-1])
            elif child.type == "assignment_pattern":
                left = child.child_by_field_name("left")
                right = child.child_by_field_name("right")
                name = self._node_text(left) if left else ""
                default = self._node_text(right) if right else ""

            if not name:
                continue

            param_qn = f"{fn_qn}.{name}"
            p = ParameterNode(
                qualified_name=param_qn,
                name=name,
                type_hint=type_hint,
                default_value=default,
                position=position,
            )
            params.append(p)
            self._result.parameters.append(p)
            self._result.edges.append(Edge("HAS_PARAMETER", fn_qn, param_qn))
            position += 1

        return params

    # ── Calls ─────────────────────────────────────────────────

    def _extract_calls(self, body: Node, caller_qn: str) -> None:
        builtin = {"console", "Math", "JSON", "Object", "Array", "Promise",
                    "parseInt", "parseFloat", "setTimeout", "setInterval",
                    "clearTimeout", "clearInterval", "require"}
        for node in self._walk(body):
            if node.type == "call_expression":
                func = node.child_by_field_name("function")
                if not func:
                    continue
                callee = self._node_text(func)
                first_part = callee.split(".")[0] if "." in callee else callee
                if first_part not in builtin:
                    target = callee.split(".")[-1] if "." in callee else callee
                    self._result.edges.append(Edge("CALLS", caller_qn, target))

    # ── Imports ───────────────────────────────────────────────

    def _extract_import(self, node: Node) -> None:
        source_node = node.child_by_field_name("source")
        if not source_node:
            return
        module_path = self._node_text(source_node).strip("'\"")
        self._result.edges.append(
            Edge("IMPORTS", self._file_path, module_path, {"imported_name": "", "alias": ""})
        )

    # ── Variables ─────────────────────────────────────────────

    def _extract_variable_declaration(self, node: Node, parent_qn: str) -> None:
        for child in node.children:
            if child.type == "variable_declarator":
                name_node = child.child_by_field_name("name")
                name = self._node_text(name_node) if name_node else ""
                if not name:
                    continue
                value_node = child.child_by_field_name("value")
                if value_node and value_node.type in ("arrow_function", "function"):
                    fn_qn = f"{parent_qn}.{name}"
                    body = value_node.child_by_field_name("body")
                    params_node = value_node.child_by_field_name("parameters")
                    body_text = self._node_text(body) if body else ""
                    fn = FunctionNode(
                        qualified_name=fn_qn,
                        name=name,
                        signature=f"const {name} = ({self._node_text(params_node) if params_node else ''}) =>",
                        body_hash=hashlib.md5(body_text.encode()).hexdigest(),
                        start_line=value_node.start_point[0] + 1,
                        end_line=value_node.end_point[0] + 1,
                        is_async=self._has_modifier(value_node, "async"),
                        param_count=self._count_params(params_node),
                    )
                    self._result.functions.append(fn)
                    self._result.function_bodies[fn_qn] = body_text
                    self._result.edges.append(Edge("CONTAINS", self._file_path, fn_qn))
                    if body:
                        self._extract_calls(body, fn_qn)
                else:
                    type_ann = child.child_by_field_name("type")
                    var_qn = f"{parent_qn}.{name}"
                    self._result.variables.append(VariableNode(
                        qualified_name=var_qn,
                        name=name,
                        type_hint=self._node_text(type_ann).lstrip(":").strip() if type_ann else "",
                        scope="module",
                        is_class_field=False,
                    ))
                    self._result.edges.append(Edge("CONTAINS", self._file_path, var_qn))

    def _extract_class_field(self, node: Node, cls_qn: str) -> None:
        name_node = node.child_by_field_name("name")
        name = self._node_text(name_node) if name_node else ""
        if not name:
            return
        type_ann = node.child_by_field_name("type")
        var_qn = f"{cls_qn}.{name}"
        self._result.variables.append(VariableNode(
            qualified_name=var_qn,
            name=name,
            type_hint=self._node_text(type_ann).lstrip(":").strip() if type_ann else "",
            scope="class",
            is_class_field=True,
        ))
        self._result.edges.append(Edge("HAS_PROPERTY", cls_qn, var_qn))

    # ── Helpers ───────────────────────────────────────────────

    def _node_text(self, node: Node | None) -> str:
        if node is None:
            return ""
        return self._source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")

    def _extract_jsdoc(self, node: Node) -> str:
        prev = node.prev_sibling
        if prev and prev.type == "comment":
            text = self._node_text(prev)
            if text.startswith("/**"):
                lines = text.split("\n")
                cleaned = []
                for line in lines:
                    line = line.strip().lstrip("/*").rstrip("*/").strip()
                    if line and not line.startswith("@"):
                        cleaned.append(line)
                return " ".join(cleaned)
        return ""

    def _fn_qn(self, node: Node, parent_qn: str) -> str:
        name = self._node_text(node.child_by_field_name("name"))
        return f"{parent_qn}.{name}"

    def _cls_qn(self, node: Node, parent_qn: str) -> str:
        name = self._node_text(node.child_by_field_name("name"))
        return f"{parent_qn}.{name}"

    @staticmethod
    def _has_modifier(node: Node, modifier: str) -> bool:
        for child in node.children:
            if child.type == modifier:
                return True
        return False

    @staticmethod
    def _find_child_type(node: Node, type_name: str) -> Node | None:
        for child in node.children:
            if child.type == type_name:
                return child
        return None

    @staticmethod
    def _count_branches(node: Node | None) -> int:
        if not node:
            return 0
        count = 0
        branch_types = {"if_statement", "else_clause", "for_statement", "for_in_statement",
                        "while_statement", "do_statement", "switch_case", "try_statement",
                        "catch_clause", "ternary_expression", "binary_expression"}
        stack = [node]
        while stack:
            n = stack.pop()
            if n.type in branch_types:
                count += 1
            stack.extend(n.children)
        return count

    @staticmethod
    def _count_params(params_node: Node | None) -> int:
        if not params_node:
            return 0
        return sum(1 for c in params_node.children
                   if c.type in ("required_parameter", "optional_parameter",
                                  "rest_parameter", "identifier"))

    @staticmethod
    def _walk(node: Node):
        stack = [node]
        while stack:
            n = stack.pop()
            yield n
            stack.extend(reversed(n.children))
