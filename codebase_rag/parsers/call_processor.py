from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from loguru import logger
from tree_sitter import Node, QueryCursor

from .. import constants as cs
from .. import logs as ls
from ..language_spec import LanguageSpec
from ..services import IngestorProtocol
from ..types_defs import FunctionRegistryTrieProtocol, LanguageQueries
from .call_resolver import CallResolver
from .cpp import utils as cpp_utils
from .import_processor import ImportProcessor
from .type_inference import TypeInferenceEngine
from .utils import get_function_captures, is_method_node


@dataclass
class CallContext:
    """Context for a group of calls sharing the same caller."""

    caller_node: Node
    caller_qn: str
    caller_type: str
    class_context: str | None = None
    call_nodes: list[Node] = field(default_factory=list)


class CallProcessor:
    def __init__(
        self,
        ingestor: IngestorProtocol,
        repo_path: Path,
        project_name: str,
        function_registry: FunctionRegistryTrieProtocol,
        import_processor: ImportProcessor,
        type_inference: TypeInferenceEngine,
        class_inheritance: dict[str, list[str]],
    ) -> None:
        self.ingestor = ingestor
        self.repo_path = repo_path
        self.project_name = project_name

        self._resolver = CallResolver(
            function_registry=function_registry,
            import_processor=import_processor,
            type_inference=type_inference,
            class_inheritance=class_inheritance,
        )

    def _get_node_name(self, node: Node, field: str = cs.FIELD_NAME) -> str | None:
        name_node = node.child_by_field_name(field)
        if not name_node:
            return None
        text = name_node.text
        return None if text is None else text.decode(cs.ENCODING_UTF8)

    def process_calls_in_file(
        self,
        file_path: Path,
        root_node: Node,
        language: cs.SupportedLanguage,
        queries: dict[cs.SupportedLanguage, LanguageQueries],
    ) -> None:
        """
        Process all function calls in a file.

        Uses an optimized approach:
        1. Collect all functions and classes in one pass each
        2. Build caller contexts (functions, methods, module)
        3. Collect all calls in a single pass
        4. Attribute calls to their containing context
        5. Process calls grouped by context
        """
        relative_path = file_path.relative_to(self.repo_path)
        logger.debug(ls.CALL_PROCESSING_FILE.format(path=relative_path))

        try:
            module_qn = cs.SEPARATOR_DOT.join(
                [self.project_name] + list(relative_path.with_suffix("").parts)
            )
            if file_path.name in (cs.INIT_PY, cs.MOD_RS):
                module_qn = cs.SEPARATOR_DOT.join(
                    [self.project_name] + list(relative_path.parent.parts)
                )

            # Build all caller contexts
            contexts = self._build_caller_contexts(
                root_node, module_qn, language, queries
            )

            # Collect all calls in a single pass
            calls_query = queries[language].get(cs.QUERY_CALLS)
            if not calls_query:
                return

            cursor = QueryCursor(calls_query)
            captures = cursor.captures(root_node)
            all_calls = [n for n in captures.get(cs.CAPTURE_CALL, []) if isinstance(n, Node)]

            # Attribute calls to contexts and process
            self._attribute_and_process_calls(
                all_calls, contexts, module_qn, language, queries
            )

        except Exception as e:
            logger.error(ls.CALL_PROCESSING_FAILED.format(path=file_path, error=e))

    def _node_key(self, node: Node) -> tuple[int, int]:
        """
        Generate a unique key for a tree-sitter node.

        Uses byte range as identifier since tree-sitter creates new Python
        wrapper objects for each query, making id() unreliable.
        """
        return (node.start_byte, node.end_byte)

    def _build_caller_contexts(
        self,
        root_node: Node,
        module_qn: str,
        language: cs.SupportedLanguage,
        queries: dict[cs.SupportedLanguage, LanguageQueries],
    ) -> dict[tuple[int, int], CallContext]:
        """
        Build a mapping from node position to CallContext for all callers.

        Returns a dict mapping (start_byte, end_byte) to CallContext for:
        - All standalone functions
        - All methods within classes
        - The module itself (root node)
        """
        contexts: dict[tuple[int, int], CallContext] = {}

        # Add module context
        contexts[self._node_key(root_node)] = CallContext(
            caller_node=root_node,
            caller_qn=module_qn,
            caller_type=cs.NodeLabel.MODULE,
        )

        # Collect functions
        result = get_function_captures(root_node, language, queries)
        if result:
            lang_config, captures = result
            for func_node in captures.get(cs.CAPTURE_FUNCTION, []):
                if not isinstance(func_node, Node):
                    continue
                if self._is_method(func_node, lang_config):
                    continue

                if language == cs.SupportedLanguage.CPP:
                    func_name = cpp_utils.extract_function_name(func_node)
                else:
                    func_name = self._get_node_name(func_node)
                if not func_name:
                    continue

                func_qn = self._build_nested_qualified_name(
                    func_node, module_qn, func_name, lang_config
                )
                if func_qn:
                    contexts[self._node_key(func_node)] = CallContext(
                        caller_node=func_node,
                        caller_qn=func_qn,
                        caller_type=cs.NodeLabel.FUNCTION,
                    )

        # Collect classes and their methods
        class_query = queries[language].get(cs.QUERY_CLASSES)
        if class_query:
            cursor = QueryCursor(class_query)
            captures = cursor.captures(root_node)
            for class_node in captures.get(cs.CAPTURE_CLASS, []):
                if not isinstance(class_node, Node):
                    continue
                class_name = self._get_class_name_for_node(class_node, language)
                if not class_name:
                    continue
                class_qn = f"{module_qn}{cs.SEPARATOR_DOT}{class_name}"

                body_node = class_node.child_by_field_name(cs.FIELD_BODY)
                if not body_node:
                    continue

                # Collect methods in this class
                method_query = queries[language].get(cs.QUERY_FUNCTIONS)
                if method_query:
                    method_cursor = QueryCursor(method_query)
                    method_captures = method_cursor.captures(body_node)
                    for method_node in method_captures.get(cs.CAPTURE_FUNCTION, []):
                        if not isinstance(method_node, Node):
                            continue
                        method_name = self._get_node_name(method_node)
                        if not method_name:
                            continue
                        method_qn = f"{class_qn}{cs.SEPARATOR_DOT}{method_name}"
                        contexts[self._node_key(method_node)] = CallContext(
                            caller_node=method_node,
                            caller_qn=method_qn,
                            caller_type=cs.NodeLabel.METHOD,
                            class_context=class_qn,
                        )

        return contexts

    def _attribute_and_process_calls(
        self,
        all_calls: list[Node],
        contexts: dict[tuple[int, int], CallContext],
        module_qn: str,
        language: cs.SupportedLanguage,
        queries: dict[cs.SupportedLanguage, LanguageQueries],
    ) -> None:
        """
        Attribute each call to its containing context and process.

        For each call, walks up the AST to find the nearest containing
        function/method, then processes the call in that context.
        """
        # Group calls by their containing context
        for call_node in all_calls:
            context = self._find_containing_context(call_node, contexts)
            if context:
                context.call_nodes.append(call_node)

        # Process calls for each context
        for context in contexts.values():
            if not context.call_nodes:
                continue

            # Build local variable types for this context
            local_var_types = self._resolver.type_inference.build_local_variable_type_map(
                context.caller_node, module_qn, language
            )

            logger.debug(
                ls.CALL_FOUND_NODES.format(
                    count=len(context.call_nodes),
                    language=language,
                    caller=context.caller_qn,
                )
            )

            for call_node in context.call_nodes:
                self._process_single_call(
                    call_node,
                    context,
                    module_qn,
                    language,
                    local_var_types,
                )

    def _find_containing_context(
        self, call_node: Node, contexts: dict[tuple[int, int], CallContext]
    ) -> CallContext | None:
        """
        Find the innermost context containing this call node.

        Walks up the AST from the call node to find the nearest
        function/method that contains it.
        """
        current = call_node.parent
        while current is not None:
            key = self._node_key(current)
            if key in contexts:
                return contexts[key]
            current = current.parent
        return None

    def _process_single_call(
        self,
        call_node: Node,
        context: CallContext,
        module_qn: str,
        language: cs.SupportedLanguage,
        local_var_types: dict[str, str],
    ) -> None:
        """Process a single call node within its context."""
        call_name = self._get_call_target_name(call_node)
        if not call_name:
            return

        if (
            language == cs.SupportedLanguage.JAVA
            and call_node.type == cs.TS_METHOD_INVOCATION
        ):
            callee_info = self._resolver.resolve_java_method_call(
                call_node, module_qn, local_var_types
            )
        else:
            callee_info = self._resolver.resolve_function_call(
                call_name, module_qn, local_var_types, context.class_context
            )

        if callee_info:
            callee_type, callee_qn = callee_info
        elif builtin_info := self._resolver.resolve_builtin_call(call_name):
            callee_type, callee_qn = builtin_info
        elif operator_info := self._resolver.resolve_cpp_operator_call(
            call_name, module_qn
        ):
            callee_type, callee_qn = operator_info
        else:
            return

        logger.debug(
            ls.CALL_FOUND.format(
                caller=context.caller_qn,
                call_name=call_name,
                callee_type=callee_type,
                callee_qn=callee_qn,
            )
        )

        self.ingestor.ensure_relationship_batch(
            (context.caller_type, cs.KEY_QUALIFIED_NAME, context.caller_qn),
            cs.RelationshipType.CALLS,
            (callee_type, cs.KEY_QUALIFIED_NAME, callee_qn),
        )

    def _get_rust_impl_class_name(self, class_node: Node) -> str | None:
        class_name = self._get_node_name(class_node, cs.FIELD_TYPE)
        if class_name:
            return class_name
        return next(
            (
                child.text.decode(cs.ENCODING_UTF8)
                for child in class_node.children
                if child.type == cs.TS_TYPE_IDENTIFIER and child.is_named and child.text
            ),
            None,
        )

    def _get_class_name_for_node(
        self, class_node: Node, language: cs.SupportedLanguage
    ) -> str | None:
        if language == cs.SupportedLanguage.RUST and class_node.type == cs.TS_IMPL_ITEM:
            return self._get_rust_impl_class_name(class_node)
        return self._get_node_name(class_node)

    def _get_call_target_name(self, call_node: Node) -> str | None:
        if func_child := call_node.child_by_field_name(cs.TS_FIELD_FUNCTION):
            match func_child.type:
                case (
                    cs.TS_IDENTIFIER
                    | cs.TS_ATTRIBUTE
                    | cs.TS_MEMBER_EXPRESSION
                    | cs.CppNodeType.QUALIFIED_IDENTIFIER
                    | cs.TS_SCOPED_IDENTIFIER
                ):
                    if func_child.text is not None:
                        return str(func_child.text.decode(cs.ENCODING_UTF8))
                case cs.TS_CPP_FIELD_EXPRESSION:
                    field_node = func_child.child_by_field_name(cs.FIELD_FIELD)
                    if field_node and field_node.text:
                        return str(field_node.text.decode(cs.ENCODING_UTF8))
                case cs.TS_PARENTHESIZED_EXPRESSION:
                    return self._get_iife_target_name(func_child)

        match call_node.type:
            case (
                cs.TS_CPP_BINARY_EXPRESSION
                | cs.TS_CPP_UNARY_EXPRESSION
                | cs.TS_CPP_UPDATE_EXPRESSION
            ):
                operator_node = call_node.child_by_field_name(cs.FIELD_OPERATOR)
                if operator_node and operator_node.text:
                    operator_text = operator_node.text.decode(cs.ENCODING_UTF8)
                    return cpp_utils.convert_operator_symbol_to_name(operator_text)
            case cs.TS_METHOD_INVOCATION:
                object_node = call_node.child_by_field_name(cs.FIELD_OBJECT)
                name_node = call_node.child_by_field_name(cs.FIELD_NAME)
                if name_node and name_node.text:
                    method_name = str(name_node.text.decode(cs.ENCODING_UTF8))
                    if not object_node or not object_node.text:
                        return method_name
                    object_text = str(object_node.text.decode(cs.ENCODING_UTF8))
                    return f"{object_text}{cs.SEPARATOR_DOT}{method_name}"

        if name_node := call_node.child_by_field_name(cs.FIELD_NAME):
            if name_node.text is not None:
                return str(name_node.text.decode(cs.ENCODING_UTF8))

        return None

    def _get_iife_target_name(self, parenthesized_expr: Node) -> str | None:
        for child in parenthesized_expr.children:
            match child.type:
                case cs.TS_FUNCTION_EXPRESSION:
                    return f"{cs.IIFE_FUNC_PREFIX}{child.start_point[0]}_{child.start_point[1]}"
                case cs.TS_ARROW_FUNCTION:
                    return f"{cs.IIFE_ARROW_PREFIX}{child.start_point[0]}_{child.start_point[1]}"
        return None

    def _build_nested_qualified_name(
        self,
        func_node: Node,
        module_qn: str,
        func_name: str,
        lang_config: LanguageSpec,
    ) -> str | None:
        path_parts: list[str] = []
        current = func_node.parent

        if not isinstance(current, Node):
            logger.warning(
                ls.CALL_UNEXPECTED_PARENT.format(
                    node=func_node, parent_type=type(current)
                )
            )
            return None

        while current and current.type not in lang_config.module_node_types:
            if current.type in lang_config.function_node_types:
                if name_node := current.child_by_field_name(cs.FIELD_NAME):
                    text = name_node.text
                    if text is not None:
                        path_parts.append(text.decode(cs.ENCODING_UTF8))
            elif current.type in lang_config.class_node_types:
                return None

            current = current.parent

        path_parts.reverse()
        if path_parts:
            return f"{module_qn}{cs.SEPARATOR_DOT}{cs.SEPARATOR_DOT.join(path_parts)}{cs.SEPARATOR_DOT}{func_name}"
        return f"{module_qn}{cs.SEPARATOR_DOT}{func_name}"

    def _is_method(self, func_node: Node, lang_config: LanguageSpec) -> bool:
        return is_method_node(func_node, lang_config)
