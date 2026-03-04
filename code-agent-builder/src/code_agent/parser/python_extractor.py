"""Rule-based Python AST extractor using tree-sitter."""

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


class PythonExtractor(BaseExtractor):

    def supported_extensions(self) -> list[str]:
        return [".py"]

    def extract(self, source: bytes, file_path: str, module_qn: str) -> ExtractionResult:
        from tree_sitter import Parser
        import tree_sitter_python as tspython
        from tree_sitter import Language

        lang = Language(tspython.language())
        parser = Parser(lang)
        tree = parser.parse(source)

        self._source = source
        self._file_path = file_path
        self._module_qn = module_qn
        self._result = ExtractionResult()

        self._walk_module(tree.root_node)
        return self._result

    # ── Module-level walk ─────────────────────────────────────

    def _walk_module(self, root: Node) -> None:
        self._walk_module_block(root)

    def _walk_module_block(self, block: Node) -> None:
        """Walk a block at module level, recursing into try/except/if blocks."""
        for child in block.children:
            if child.type == "function_definition":
                self._extract_function(child, self._module_qn, is_method=False)
                self._result.edges.append(Edge(
                    "CONTAINS", self._file_path, self._fn_qn(child, self._module_qn),
                    from_table="Module", to_table="Function",
                ))

            elif child.type == "class_definition":
                self._extract_class(child, self._module_qn)
                cls_qn = self._cls_qn(child, self._module_qn)
                self._result.edges.append(Edge(
                    "CONTAINS", self._file_path, cls_qn,
                    from_table="Module", to_table="Class",
                ))

            elif child.type == "decorated_definition":
                inner = self._get_decorated_inner(child)
                if inner and inner.type == "function_definition":
                    self._extract_decorators(child, self._fn_qn(inner, self._module_qn))
                    self._extract_function(inner, self._module_qn, is_method=False)
                    self._result.edges.append(Edge(
                        "CONTAINS", self._file_path, self._fn_qn(inner, self._module_qn),
                        from_table="Module", to_table="Function",
                    ))
                elif inner and inner.type == "class_definition":
                    self._extract_decorators(child, self._cls_qn(inner, self._module_qn))
                    self._extract_class(inner, self._module_qn)
                    cls_qn = self._cls_qn(inner, self._module_qn)
                    self._result.edges.append(Edge(
                        "CONTAINS", self._file_path, cls_qn,
                        from_table="Module", to_table="Class",
                    ))

            elif child.type in ("import_statement", "import_from_statement"):
                self._extract_import(child)

            elif child.type == "expression_statement":
                assign = child.children[0] if child.children else None
                if assign and assign.type == "assignment":
                    self._extract_module_variable(assign)

            elif child.type in ("try_statement", "if_statement"):
                self._walk_nested_module_block(child)

    def _walk_nested_module_block(self, node: Node) -> None:
        """Recurse into try/except/if blocks to extract imports and top-level definitions."""
        for child in node.children:
            if child.type == "block":
                self._walk_module_block(child)
            elif child.type in ("except_clause", "else_clause", "elif_clause",
                                "finally_clause"):
                for sub in child.children:
                    if sub.type == "block":
                        self._walk_module_block(sub)
            elif child.type in ("try_statement", "if_statement"):
                self._walk_nested_module_block(child)

    # ── Class ─────────────────────────────────────────────────

    def _extract_class(self, node: Node, parent_qn: str) -> None:
        name = self._node_text(node.child_by_field_name("name"))
        cls_qn = f"{parent_qn}.{name}"
        docstring = self._extract_docstring(node)

        bases = node.child_by_field_name("superclasses")
        is_abstract = False
        if bases:
            for arg in bases.children:
                base_name = self._node_text(arg)
                if base_name and base_name not in (",", "(", ")"):
                    if "ABC" in base_name or "Abstract" in base_name:
                        is_abstract = True
                    self._result.edges.append(Edge("INHERITS", cls_qn, base_name))

        body = node.child_by_field_name("body")
        cls_node = ClassNode(
            qualified_name=cls_qn,
            name=name,
            docstring=docstring,
            start_line=node.start_point[0] + 1,
            end_line=node.end_point[0] + 1,
            is_abstract=is_abstract,
            complexity=self._count_branches(body) if body else 0,
        )
        self._result.classes.append(cls_node)

        if body:
            self._walk_class_body(body, cls_qn)

    def _walk_class_body(self, body: Node, cls_qn: str) -> None:
        for child in body.children:
            if child.type == "function_definition":
                self._extract_function(child, cls_qn, is_method=True)
                self._result.edges.append(Edge(
                    "HAS_METHOD", cls_qn, self._fn_qn(child, cls_qn),
                    from_table="Class", to_table="Function",
                ))

            elif child.type == "decorated_definition":
                inner = self._get_decorated_inner(child)
                if inner and inner.type == "function_definition":
                    fn_qn = self._fn_qn(inner, cls_qn)
                    self._extract_decorators(child, fn_qn)
                    self._extract_function(inner, cls_qn, is_method=True)
                    self._result.edges.append(Edge(
                        "HAS_METHOD", cls_qn, fn_qn,
                        from_table="Class", to_table="Function",
                    ))

            elif child.type == "expression_statement":
                assign = child.children[0] if child.children else None
                if assign and assign.type == "assignment":
                    self._extract_class_variable(assign, cls_qn)

    # ── Function ──────────────────────────────────────────────

    def _extract_function(self, node: Node, parent_qn: str, is_method: bool) -> None:
        name = self._node_text(node.child_by_field_name("name"))
        fn_qn = f"{parent_qn}.{name}"
        docstring = self._extract_docstring(node)

        params_node = node.child_by_field_name("parameters")
        params = self._extract_parameters(params_node, fn_qn, is_method)

        return_ann = node.child_by_field_name("return_type")
        return_type = self._node_text(return_ann) if return_ann else ""

        body = node.child_by_field_name("body")
        body_text = self._node_text(body) if body else ""
        body_hash = hashlib.md5(body_text.encode()).hexdigest()

        is_async = any(c.type == "async" for c in (node.prev_sibling,) if c) or \
                   node.parent and node.parent.type == "decorated_definition" and \
                   any(c.type == "async" for c in node.parent.children)

        fn_node = FunctionNode(
            qualified_name=fn_qn,
            name=name,
            signature=self._build_signature(name, params_node, return_ann),
            docstring=docstring,
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
            self._extract_nested_functions(body, fn_qn)

    # ── Nested Functions ──────────────────────────────────────

    def _extract_nested_functions(self, body: Node, parent_qn: str) -> None:
        """Extract function definitions nested inside another function body."""
        for child in body.children:
            if child.type == "function_definition":
                self._extract_function(child, parent_qn, is_method=False)
                self._result.edges.append(Edge(
                    "CONTAINS", self._file_path, self._fn_qn(child, parent_qn),
                    from_table="Module", to_table="Function",
                ))
            elif child.type == "decorated_definition":
                inner = self._get_decorated_inner(child)
                if inner and inner.type == "function_definition":
                    fn_qn = self._fn_qn(inner, parent_qn)
                    self._extract_decorators(child, fn_qn)
                    self._extract_function(inner, parent_qn, is_method=False)
                    self._result.edges.append(Edge(
                        "CONTAINS", self._file_path, fn_qn,
                        from_table="Module", to_table="Function",
                    ))

    # ── Parameters ────────────────────────────────────────────

    def _extract_parameters(self, params_node: Node | None, fn_qn: str, is_method: bool) -> list[ParameterNode]:
        if not params_node:
            return []

        params: list[ParameterNode] = []
        position = 0
        for child in params_node.children:
            if child.type not in ("identifier", "typed_parameter", "default_parameter", "typed_default_parameter",
                                   "list_splat_pattern", "dictionary_splat_pattern"):
                continue

            name = ""
            type_hint = ""
            default = ""

            if child.type == "identifier":
                name = self._node_text(child)
            elif child.type == "typed_parameter":
                name = self._node_text(child.children[0]) if child.children else ""
                type_node = child.child_by_field_name("type")
                type_hint = self._node_text(type_node) if type_node else ""
            elif child.type == "default_parameter":
                name_node = child.child_by_field_name("name")
                name = self._node_text(name_node) if name_node else ""
                val_node = child.child_by_field_name("value")
                default = self._node_text(val_node) if val_node else ""
            elif child.type == "typed_default_parameter":
                name_node = child.child_by_field_name("name")
                name = self._node_text(name_node) if name_node else ""
                type_node = child.child_by_field_name("type")
                type_hint = self._node_text(type_node) if type_node else ""
                val_node = child.child_by_field_name("value")
                default = self._node_text(val_node) if val_node else ""
            elif child.type in ("list_splat_pattern", "dictionary_splat_pattern"):
                name = "*" + self._node_text(child.children[-1]) if child.children else ""

            if not name or (is_method and name == "self" and position == 0):
                position += 1
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
        """Walk the function body to find call expressions."""
        for node in self._walk(body):
            if node.type == "call":
                func = node.child_by_field_name("function")
                if not func:
                    continue
                callee = self._node_text(func)
                if callee and not callee.startswith(("print", "len", "str", "int", "float",
                                                      "list", "dict", "set", "tuple", "bool",
                                                      "isinstance", "type", "range", "enumerate",
                                                      "zip", "map", "filter", "sorted", "super")):
                    target = callee.split(".")[-1] if "." in callee else callee
                    self._result.edges.append(Edge("CALLS", caller_qn, target))

    # ── Imports ───────────────────────────────────────────────

    def _extract_import(self, node: Node) -> None:
        if node.type == "import_from_statement":
            module_name_node = node.child_by_field_name("module_name")
            module_name = self._node_text(module_name_node) if module_name_node else ""
            if module_name:
                for child in node.children:
                    if child.type == "dotted_name" and child != module_name_node:
                        imported = self._node_text(child)
                        self._result.edges.append(
                            Edge("IMPORTS", self._file_path, module_name,
                                 {"imported_name": imported, "alias": ""})
                        )
                    elif child.type == "aliased_import":
                        name_node = child.child_by_field_name("name")
                        alias_node = child.child_by_field_name("alias")
                        imported = self._node_text(name_node) if name_node else ""
                        alias = self._node_text(alias_node) if alias_node else ""
                        self._result.edges.append(
                            Edge("IMPORTS", self._file_path, module_name,
                                 {"imported_name": imported, "alias": alias})
                        )
        elif node.type == "import_statement":
            for child in node.children:
                if child.type == "dotted_name":
                    module_name = self._node_text(child)
                    self._result.edges.append(
                        Edge("IMPORTS", self._file_path, module_name,
                             {"imported_name": "", "alias": ""})
                    )
                elif child.type == "aliased_import":
                    name_node = child.child_by_field_name("name")
                    alias_node = child.child_by_field_name("alias")
                    module_name = self._node_text(name_node) if name_node else ""
                    alias = self._node_text(alias_node) if alias_node else ""
                    self._result.edges.append(
                        Edge("IMPORTS", self._file_path, module_name,
                             {"imported_name": "", "alias": alias})
                    )

    # ── Variables ─────────────────────────────────────────────

    def _extract_module_variable(self, assign: Node) -> None:
        targets = assign.child_by_field_name("left")
        if not targets:
            return
        name = self._node_text(targets)
        if name and name.isupper():
            var_qn = f"{self._module_qn}.{name}"
            type_ann = assign.child_by_field_name("type")
            self._result.variables.append(VariableNode(
                qualified_name=var_qn,
                name=name,
                type_hint=self._node_text(type_ann) if type_ann else "",
                scope="module",
                is_class_field=False,
            ))
            self._result.edges.append(Edge(
                "CONTAINS", self._file_path, var_qn,
                from_table="Module", to_table="Variable",
            ))

    def _extract_class_variable(self, assign: Node, cls_qn: str) -> None:
        targets = assign.child_by_field_name("left")
        if not targets:
            return
        name = self._node_text(targets)
        if name:
            var_qn = f"{cls_qn}.{name}"
            type_ann = assign.child_by_field_name("type")
            self._result.variables.append(VariableNode(
                qualified_name=var_qn,
                name=name,
                type_hint=self._node_text(type_ann) if type_ann else "",
                scope="class",
                is_class_field=True,
            ))
            self._result.edges.append(Edge(
                "HAS_PROPERTY", cls_qn, var_qn,
                from_table="Class", to_table="Variable",
            ))

    # ── Decorators ────────────────────────────────────────────

    def _extract_decorators(self, decorated_node: Node, target_qn: str) -> list[DecoratorNode]:
        decorators: list[DecoratorNode] = []
        for child in decorated_node.children:
            if child.type == "decorator":
                name_parts = []
                has_args = False
                for part in child.children:
                    if part.type == "@":
                        continue
                    if part.type == "argument_list":
                        has_args = True
                    elif part.type in ("identifier", "dotted_name", "attribute"):
                        name_parts.append(self._node_text(part))
                dec_name = ".".join(name_parts) if name_parts else self._node_text(child)
                dec_qn = f"@{dec_name}"
                d = DecoratorNode(qualified_name=dec_qn, name=dec_name, has_args=has_args)
                decorators.append(d)
                self._result.decorators.append(d)
                self._result.edges.append(Edge("DECORATED_BY", target_qn, dec_qn))
        return decorators

    # ── Helpers ───────────────────────────────────────────────

    def _node_text(self, node: Node | None) -> str:
        if node is None:
            return ""
        return self._source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")

    def _extract_docstring(self, node: Node) -> str:
        body = node.child_by_field_name("body")
        if not body or not body.children:
            return ""
        first = body.children[0]
        if first.type == "expression_statement" and first.children:
            expr = first.children[0]
            if expr.type == "string":
                text = self._node_text(expr)
                return text.strip("\"'").strip()
        return ""

    def _build_signature(self, name: str, params: Node | None, return_ann: Node | None) -> str:
        params_text = self._node_text(params) if params else "()"
        ret = f" -> {self._node_text(return_ann)}" if return_ann else ""
        return f"def {name}{params_text}{ret}"

    def _fn_qn(self, node: Node, parent_qn: str) -> str:
        name = self._node_text(node.child_by_field_name("name"))
        return f"{parent_qn}.{name}"

    def _cls_qn(self, node: Node, parent_qn: str) -> str:
        name = self._node_text(node.child_by_field_name("name"))
        return f"{parent_qn}.{name}"

    @staticmethod
    def _count_branches(node: Node | None) -> int:
        """Approximate cyclomatic complexity by counting branch nodes."""
        if not node:
            return 0
        count = 0
        branch_types = {"if_statement", "elif_clause", "for_statement", "while_statement",
                        "try_statement", "except_clause", "with_statement",
                        "conditional_expression", "boolean_operator"}
        stack = [node]
        while stack:
            n = stack.pop()
            if n.type in branch_types:
                count += 1
            stack.extend(n.children)
        return count

    @staticmethod
    def _walk(node: Node):
        stack = [node]
        while stack:
            n = stack.pop()
            yield n
            stack.extend(reversed(n.children))

    @staticmethod
    def _get_decorated_inner(node: Node) -> Node | None:
        for child in node.children:
            if child.type in ("function_definition", "class_definition"):
                return child
        return None
