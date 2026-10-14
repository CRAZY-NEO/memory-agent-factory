"""Derive small scalar formula cases using an AST interpreter, never executing repository code."""

import ast
import math
import operator

OPERATIONS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
              ast.Div: operator.truediv}
MATH = {'log': math.log, 'log1p': math.log1p, 'sqrt': math.sqrt}


def expression(node, values):
    if isinstance(node, ast.Constant) and type(node.value) in {int, float}:
        return node.value
    if isinstance(node, ast.Name) and node.id in values:
        return values[node.id]
    if isinstance(node, ast.BinOp) and type(node.op) in OPERATIONS:
        return OPERATIONS[type(node.op)](expression(node.left, values), expression(node.right, values))
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        return expression(node.operand, values) * (-1 if isinstance(node.op, ast.USub) else 1)
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name) and node.func.value.id == 'math'
            and node.func.attr in MATH and len(node.args) == 1 and not node.keywords):
        return MATH[node.func.attr](expression(node.args[0], values))
    raise ValueError('Unsupported source formula expression')


def scalar_formula(code, parameters):
    tree = ast.parse(code)
    if len(tree.body) != 1 or not isinstance(tree.body[0], ast.FunctionDef):
        raise ValueError('Expected one source scoring function')
    function = tree.body[0]
    if function.decorator_list or function.args.vararg or function.args.kwarg or function.args.kwonlyargs:
        raise ValueError('Unsupported decorated or variadic source kernel')
    if len(function.body) > 20:
        raise ValueError('Source scalar kernel exceeds interpretation budget')
    values = dict(parameters)
    if any(argument.arg not in values for argument in function.args.args):
        raise ValueError('Unsupported source parameter mapping')
    for statement in function.body:
        if isinstance(statement, ast.Assign) and len(statement.targets) == 1 and isinstance(statement.targets[0], ast.Name):
            values[statement.targets[0].id] = expression(statement.value, values)
        elif isinstance(statement, ast.Return):
            result = expression(statement.value, values)
            if not math.isfinite(result) or result < 0:
                raise ValueError('Source formula produced invalid score')
            return result
        else:
            raise ValueError('Unsupported source scoring statement')
    raise ValueError('Source scoring function lacks return')


def derive_term_contract(functions, defaults):
    """Recognize explicit tf and df kernels; unsupported forms are reported, never guessed."""
    tf_kernel = None
    idf_kernel = None
    try:
        for row in functions:
            if row.get('code_truncated'):
                continue
            function = ast.parse(row['code']).body[0]
            names = {argument.arg for argument in function.args.args}
            if names & {'tf', 'tf_array'}:
                tf_kernel = row
            elif {'df', 'N'} <= names:
                idf_kernel = row
        if tf_kernel is None or idf_kernel is None:
            raise ValueError('No supported separate scalar TF and IDF kernels')
        memories = [{'id': 'a', 'text': 'alpha alpha beta', 'source': 'public formula case'},
                    {'id': 'b', 'text': 'alpha beta', 'source': 'public formula case'},
                    {'id': 'c', 'text': 'gamma', 'source': 'public formula case'}]
        cases = []
        for query, weight in [('alpha', 1), ('alpha alpha', 2)]:
            expected = []
            for identity, tf, length in [('a', 2, 3), ('b', 1, 2), ('c', 0, 1)]:
                parameters = {**defaults, 'tf': tf, 'tf_array': tf, 'df': 2, 'N': 3,
                              'l_d': length, 'doc_len': length, 'l_avg': 2.0, 'avg_len': 2.0}
                score = scalar_formula(tf_kernel['code'], parameters) * scalar_formula(idf_kernel['code'], parameters) * weight
                if score != 0:
                    expected.append({'id': identity, 'score': score})
            expected.sort(key=lambda hit: (-hit['score'], hit['id']))
            cases.append({'query': query, 'memories': memories, 'top_k': 3, 'expected': expected})
        helpers = ['import math']
        calls = []
        bindings = {'tf': 'tf', 'tf_array': 'tf', 'df': 'df', 'N': 'N',
                    'l_d': 'doc_len', 'doc_len': 'doc_len', 'l_avg': 'avg_len', 'avg_len': 'avg_len'}
        for row, name in [(tf_kernel, 'source_tf_component'), (idf_kernel, 'source_idf_component')]:
            function = ast.parse(row['code']).body[0]
            arguments = [bindings.get(argument.arg, repr(defaults[argument.arg]) if argument.arg in defaults else None)
                         for argument in function.args.args]
            if None in arguments:
                raise ValueError('Unsupported kernel adapter argument')
            function.name = name
            function.returns = None
            function.args.defaults = []
            for argument in function.args.args:
                argument.annotation = None
            helpers.append(ast.unparse(function))
            calls.append(name + '(' + ', '.join(arguments) + ')')
        hint = ('def score_term(tf, df, doc_len, avg_len, N):\n'
                '    if avg_len <= 0 or df <= 0:\n        return 0.0\n'
                '    return ' + ' * '.join(calls))
        return {'status': 'derived_scalar_formula_cases', 'cases': cases,
                'kernel_helpers': '\n\n'.join(helpers), 'adapter_hint': hint,
                'kernels': [{'path': row['path'], 'qualified_name': row['qualified_name'], 'code': row['code']}
                            for row in [tf_kernel, idf_kernel]], 'defaults': defaults,
                'scope': 'Small public numeric examples; not a proof of full repository equivalence'}
    except (ValueError, SyntaxError, AttributeError, IndexError, TypeError, ZeroDivisionError) as error:
        return {'status': 'unsupported', 'reason': str(error), 'cases': []}
