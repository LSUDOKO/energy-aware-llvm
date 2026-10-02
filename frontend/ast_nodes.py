from typing import List, Optional

class ASTNode:
    pass

class Program(ASTNode):
    def __init__(self, functions):
        self.functions = functions

class FunctionDecl(ASTNode):
    def __init__(self, return_type, name, params, body):
        self.return_type = return_type
        self.name = name
        self.params = params
        self.body = body

class Block(ASTNode):
    def __init__(self, statements):
        self.statements = statements

class VarDecl(ASTNode):
    def __init__(self, var_type, name, init_expr):
        self.var_type = var_type
        self.name = name
        self.init_expr = init_expr

class ReturnStmt(ASTNode):
    def __init__(self, expr):
        self.expr = expr

class IfStmt(ASTNode):
    def __init__(self, condition, then_branch, else_branch):
        self.condition = condition
        self.then_branch = then_branch
        self.else_branch = else_branch

class WhileStmt(ASTNode):
    def __init__(self, condition, body):
        self.condition = condition
        self.body = body

class AssignStmt(ASTNode):
    def __init__(self, name, expr):
        self.name = name
        self.expr = expr

class BinaryOp(ASTNode):
    def __init__(self, op, left, right):
        self.op = op
        self.left = left
        self.right = right

class Number(ASTNode):
    def __init__(self, value, is_float=False):
        self.value = value
        self.is_float = is_float

class Identifier(ASTNode):
    def __init__(self, name):
        self.name = name
