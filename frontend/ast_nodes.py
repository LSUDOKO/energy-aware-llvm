"""AST node definitions for the MiniC subset.

Typed-representation step (plan Part II, stage 1): expression nodes carry
optional slots for

  * ``loc``             - (line, column) source location,
  * ``type``            - resolved type string ("i32"/"float") after lowering,
  * ``const_value``     - compile-time constant value when known,
  * ``value_category``  - always "rvalue" in the current subset.

The parser fills ``loc``; the unified semantic visitor
(``frontend/semantic_codegen.py``) annotates type/const_value during its
single traversal.
"""


class ASTNode:
    pass


class Program(ASTNode):
    def __init__(self, functions):
        self.functions = functions


class FunctionDecl(ASTNode):
    def __init__(self, return_type, name, params, body, loc=None):
        self.return_type = return_type
        self.name = name
        self.params = params  # list of (type_str, name)
        self.body = body
        self.loc = loc


class Block(ASTNode):
    def __init__(self, statements):
        self.statements = statements


class VarDecl(ASTNode):
    def __init__(self, var_type, name, init_expr, loc=None):
        self.var_type = var_type
        self.name = name
        self.init_expr = init_expr
        self.loc = loc


class ReturnStmt(ASTNode):
    def __init__(self, expr, loc=None):
        self.expr = expr
        self.loc = loc


class IfStmt(ASTNode):
    def __init__(self, condition, then_branch, else_branch, loc=None):
        self.condition = condition
        self.then_branch = then_branch
        self.else_branch = else_branch
        self.loc = loc


class WhileStmt(ASTNode):
    def __init__(self, condition, body, loc=None):
        self.condition = condition
        self.body = body
        self.loc = loc


class AssignStmt(ASTNode):
    def __init__(self, name, expr, loc=None):
        self.name = name
        self.expr = expr
        self.loc = loc


class _Expression(ASTNode):
    """Base for expression nodes carrying typed-representation slots."""

    def __init__(self, loc=None):
        self.loc = loc
        self.type = None
        self.const_value = None
        self.value_category = None


class BinaryOp(_Expression):
    def __init__(self, op, left, right, loc=None):
        super().__init__(loc)
        self.op = op
        self.left = left
        self.right = right


class UnaryOp(_Expression):
    def __init__(self, op, operand, loc=None):
        super().__init__(loc)
        self.op = op
        self.operand = operand


class CallExpr(_Expression):
    def __init__(self, name, args, loc=None):
        super().__init__(loc)
        self.name = name
        self.args = args


class Number(_Expression):
    def __init__(self, value, is_float=False, loc=None):
        super().__init__(loc)
        self.value = value
        self.is_float = is_float


class Identifier(_Expression):
    def __init__(self, name, loc=None):
        super().__init__(loc)
        self.name = name
