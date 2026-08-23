"""Convert Onshape's FeatureScript representation AST back to source text."""

from typing import Any

_BINARY_OPERATORS = {
    'AND': '&&',
    'DIVIDED_BY': '/',
    'EQUAL': '==',
    'GREATER_THAN': '>',
    'GREATER_THAN_OR_EQUAL': '>=',
    'LESS_THAN': '<',
    'LESS_THAN_OR_EQUAL': '<=',
    'MINUS': '-',
    'MODULO': '%',
    'NOT_EQUAL': '!=',
    'OR': '||',
    'PLUS': '+',
    'TIMES': '*',
}

_UNARY_OPERATORS = {
    'NEGATIVE': '-',
    'NOT': '!',
    'POSITIVE': '+',
}

_ASSIGNMENT_OPERATORS = {
    'NONE': '=',
    'PLUS': '+=',
    'MINUS': '-=',
    'TIMES': '*=',
    'DIVIDED_BY': '/=',
}

_STANDARD_TYPES = {
    'ANY': 'Anything',
    'ARRAY': 'array',
    'BOOLEAN': 'boolean',
    'BOX': 'box',
    'FUNCTION': 'function',
    'MAP': 'map',
    'NUMBER': 'number',
    'STRING': 'string',
}


class FeatureScriptUnparser:
    """Canonical unparser for the AST returned by FeatureScript representation API."""

    def unparse(self, module: dict[str, Any]) -> str:
        if module.get('btType', '').split('-')[0] != 'BTPModule':
            raise ValueError('Expected a BTPModule FeatureScript representation')
        version = self._expr(module['version'])
        top_level = '\n'.join(self._top(node) for node in module.get('topLevel', []))
        return f'FeatureScript {version};\n{top_level}\n'

    def _top(self, node: dict[str, Any]) -> str:
        node_type = self._base_type(node)
        if node_type == 'BTPTopLevelImport':
            module_id = node['moduleId']
            prefix = 'export ' if node.get('forExport') else ''
            namespace = node.get('namespace') or []
            namespace_text = f' as {self._name(namespace)}' if namespace else ''
            return (
                f'{self._annotation(node.get("annotation"), 0)}'
                f'{prefix}import(path : {self._expr(module_id["path"])}, '
                f'version : {self._expr(module_id["version"])}){namespace_text};'
            )
        if node_type == 'BTPFunctionDeclaration':
            prefix = 'export ' if node.get('forExport') else ''
            arguments = ', '.join(self._argument(argument) for argument in node.get('arguments') or [])
            returns = f' returns {self._type(node["returnType"])}' if node.get('returnType') else ''
            precondition = self._precondition(node.get('precondition'), 0)
            return (
                f'{self._annotation(node.get("annotation"), 0)}'
                f'{prefix}function {self._identifier(node["name"])}({arguments}){returns}'
                f'{precondition}\n{self._block(node["body"], 0)}'
            )
        if node_type == 'BTPTopLevelConstantDeclaration':
            prefix = 'export ' if node.get('forExport') else ''
            return prefix + self._statement(node['declaration'], 0).lstrip()
        self._unsupported(node)

    def _statement(self, node: dict[str, Any], indent: int) -> str:
        node_type = self._base_type(node)
        pad = '    ' * indent
        annotation = self._annotation(node.get('annotation'), indent)
        if node_type == 'BTPStatementAssignment':
            operator = _ASSIGNMENT_OPERATORS.get(node.get('operator'))
            if operator is None:
                raise NotImplementedError(f'Unsupported FeatureScript assignment operator: {node.get("operator")}')
            return f'{annotation}{pad}{self._lvalue(node["lvalue"])} {operator} {self._expr(node["rvalue"], indent)};'
        if node_type == 'BTPStatementBlock':
            return annotation + self._block(node, indent)
        if node_type == 'BTPStatementCompressedQuery':
            query = node.get('query', '').strip()
            return f'{annotation}{pad}{query}'
        if node_type == 'BTPStatementConstantDeclaration':
            type_text = f' is {self._type(node["type"])}' if node.get('type') else ''
            value = f' = {self._expr(node["value"], indent)}' if node.get('value') is not None else ''
            return f'{annotation}{pad}const {self._identifier(node["name"])}{type_text}{value};'
        if node_type == 'BTPStatementExpression':
            return f'{annotation}{pad}{self._expr(node["expression"], indent)};'
        if node_type == 'BTPStatementIf':
            result = (
                f'{annotation}{pad}if ({self._expr(node["condition"], indent)})\n'
                f'{self._block(node["thenBody"], indent)}'
            )
            if node.get('elseBody') is not None:
                result += f'\n{pad}else\n{self._block(node["elseBody"], indent)}'
            return result
        if node_type == 'BTPStatementReturn':
            value = f' {self._expr(node["value"], indent)}' if node.get('value') is not None else ''
            return f'{annotation}{pad}return{value};'
        if node_type == 'BTPStatementVarDeclaration':
            type_text = f' is {self._type(node["type"])}' if node.get('type') else ''
            value = f' = {self._expr(node["value"], indent)}' if node.get('value') is not None else ''
            return f'{annotation}{pad}var {self._identifier(node["name"])}{type_text}{value};'
        self._unsupported(node)

    def _block(self, node: dict[str, Any], indent: int) -> str:
        pad = '    ' * indent
        statements = node.get('statements') or []
        if not statements:
            return f'{pad}{{\n{pad}}}'
        content = '\n'.join(self._statement(statement, indent + 1) for statement in statements)
        return f'{pad}{{\n{content}\n{pad}}}'

    def _expr(self, node: dict[str, Any], indent: int = 0) -> str:
        node_type = self._base_type(node)
        if node_type == 'BTPExpressionAccess':
            separator = '?.' if node.get('isSafeNavigation') else '.'
            return f'{self._expr(node["base"], indent)}{separator}{self._identifier(node["accessor"])}'
        if node_type == 'BTPExpressionCall':
            function = node.get('functionExpression') or node.get('functionName')
            arguments = ', '.join(self._expr(argument, indent) for argument in node.get('arguments') or [])
            return f'{self._expr(function, indent)}({arguments})'
        if node_type == 'BTPExpressionFunction':
            arguments = ', '.join(self._argument(argument) for argument in node.get('arguments') or [])
            returns = f' returns {self._type(node["returnType"])}' if node.get('returnType') else ''
            if node.get('isLambda'):
                if node.get('expression') is not None:
                    return f'({arguments}) => {self._expr(node["expression"], indent)}'
                return f'({arguments}) => {self._block(node["body"], indent)}'
            precondition = self._precondition(node.get('precondition'), indent)
            return f'function({arguments}){returns}{precondition}\n{self._block(node["body"], indent)}'
        if node_type == 'BTPExpressionOperator':
            operator = node.get('operator')
            if operator in _BINARY_OPERATORS:
                return (
                    f'{self._expr(node["operand1"], indent)} {_BINARY_OPERATORS[operator]} '
                    f'{self._expr(node["operand2"], indent)}'
                )
            if operator in _UNARY_OPERATORS:
                return f'{_UNARY_OPERATORS[operator]}{self._expr(node["operand1"], indent)}'
            if operator == 'CONDITIONAL':
                return (
                    f'({self._expr(node["operand1"], indent)} ? {self._expr(node["operand2"], indent)} '
                    f': {self._expr(node["operand3"], indent)})'
                )
            raise NotImplementedError(f'Unsupported FeatureScript expression operator: {operator}')
        if node_type == 'BTPExpressionTry':
            silent = ' silent' if node.get('silent') else ''
            return f'try{silent}({self._expr(node["expression"], indent)})'
        if node_type == 'BTPExpressionVarReference':
            return self._name(node['name'])
        if node_type == 'BTPLiteralArray':
            values = ', '.join(self._expr(value, indent) for value in node.get('value') or [])
            return f'[{values}]'
        if node_type == 'BTPLiteralBoolean':
            return 'true' if node.get('value') else 'false'
        if node_type == 'BTPLiteralMap':
            entries = ', '.join(self._map_entry(entry, indent) for entry in node.get('entries') or [])
            return f'{{{entries}}}'
        if node_type == 'BTPLiteralNumber':
            text = node.get('text')
            return str(text) if text not in {None, ''} else repr(node['value'])
        if node_type == 'BTPLiteralString':
            return str(node['text'])
        self._unsupported(node)

    def _lvalue(self, node: dict[str, Any]) -> str:
        node_type = self._base_type(node)
        if node_type == 'BTPLValueAccess':
            return f'{self._lvalue(node["base"])}.{self._identifier(node["accessor"])}'
        if node_type == 'BTPLValueVarReference':
            return self._identifier(node['name'])
        self._unsupported(node)

    def _map_entry(self, node: dict[str, Any], indent: int) -> str:
        return f'{self._expr(node["key"], indent)} : {self._expr(node["value"], indent)}'

    def _argument(self, node: dict[str, Any]) -> str:
        type_text = f' is {self._type(node["type"])}' if node.get('type') else ''
        return f'{self._identifier(node["name"])}{type_text}'

    def _type(self, node: dict[str, Any]) -> str:
        node_type = self._base_type(node)
        if node_type == 'BTPTypeNameStandard':
            return _STANDARD_TYPES.get(node['type'], str(node['type']).lower())
        if node_type == 'BTPTypeNameUser':
            return self._name(node['type'])
        self._unsupported(node)

    def _annotation(self, node: dict[str, Any] | None, indent: int) -> str:
        if node is None:
            return ''
        if self._base_type(node) != 'BTPAnnotation':
            self._unsupported(node)
        return f'{"    " * indent}annotation {self._expr(node["value"], indent)}\n'

    def _precondition(self, node: dict[str, Any] | None, indent: int) -> str:
        if node is None:
            return ''
        return f'\n{"    " * indent}precondition\n{self._block(node, indent)}'

    def _name(self, node: dict[str, Any] | list[dict[str, Any]]) -> str:
        if isinstance(node, list):
            return '::'.join(self._identifier(part) for part in node)
        if self._base_type(node) == 'BTPName':
            namespace = node.get('namespace') or []
            parts = [self._identifier(part) for part in namespace]
            parts.append(self._identifier(node['identifier']))
            prefix = '::' if node.get('globalNamespace') else ''
            return prefix + '::'.join(parts)
        return self._identifier(node)

    @staticmethod
    def _identifier(node: dict[str, Any] | str) -> str:
        if isinstance(node, str):
            return node
        if 'identifier' not in node:
            raise ValueError(f'Expected a FeatureScript identifier, got {node.get("btType")}')
        identifier = node['identifier']
        if isinstance(identifier, dict):
            return FeatureScriptUnparser._identifier(identifier)
        return str(identifier)

    @staticmethod
    def _base_type(node: dict[str, Any]) -> str:
        if not isinstance(node, dict):
            raise TypeError(f'Expected FeatureScript AST object, got {type(node).__name__}')
        return str(node.get('btType', '')).split('-')[0]

    @staticmethod
    def _unsupported(node: dict[str, Any]) -> None:
        raise NotImplementedError(f'Unsupported FeatureScript AST node: {node.get("btType", "<missing btType>")}')


def unparse_featurescript_ast(module: dict[str, Any]) -> str:
    """Return canonical FeatureScript source for an Onshape BTPModule response."""
    return FeatureScriptUnparser().unparse(module)


__all__ = ['FeatureScriptUnparser', 'unparse_featurescript_ast']
