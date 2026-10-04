"""Standalone semantic checker (baseline pipeline, stage 2).

This is deliberately a *separate* AST traversal: it only checks names and
types and produces no IR. It exists to represent the "conventional pipeline"
(the plan's Part II baseline) where semantic analysis and IR generation are
distinct stages that each walk the tree. The merged/energy-aware pipeline
(frontend/semantic_codegen.py) replaces this walk + the emit walk with a
single traversal.

Checking rules for the MiniC subset:
  * variables must be declared before use, within lexical scopes,
  * redeclaration in the same scope is an error,
  * int/float arithmetic is allowed; conditionals require numeric types,
  * return statements must match the function's declared return type,
  * calls must reference declared functions with matching arity/types,
  * exactly one `main` function must exist.
"""
from __future__ import annotations

from frontend.ast_nodes import (
    AssignStmt, BinaryOp, Block, CallExpr, FunctionDecl, Identifier, IfStmt,
    Number, Program, ReturnStmt, UnaryOp, VarDecl, WhileStmt,
)


class SemanticError(Exception):
    def __init__(self, message: str, line: int | None = None, col: int | None = None):
        if line is not None:
            message = f"line {line}: {message}"
        super().__init__(message)


class Scope:
    def __init__(self, parent: "Scope | None" = None):
        self.parent = parent
        self.symbols: dict[str, str] = {}

    def declare(self, name: str, var_type: str) -> None:
        if name in self.symbols:
            raise SemanticError(f"redeclaration of '{name}' in same scope")
        self.symbols[name] = var_type

    def lookup(self, name: str) -> str | None:
        scope: Scope | None = self
        while scope:
            if name in scope.symbols:
                return scope.symbols[name]
            scope = scope.parent
        return None


def _numeric(t: str) -> bool:
    return t in ("int", "float")


class SemanticChecker:
    """Single-purpose checker: check(node) returns the node's type string."""

    def __init__(self):
        self.errors: list[str] = []
        self.functions: dict[str, tuple[str, list[str]]] = {}

    def check(self, program: Program) -> list[str]:
        """Validate the whole program; returns collected error strings."""
        self.errors = []
        self.functions = {}
        for fn in program.functions:
            if fn.name in self.functions:
                loc = getattr(fn, "loc", None)
                line = loc[0] if loc else None
                self.errors.append(f"duplicate definition of function '{fn.name}'"
                                   + (f" (line {line})" if line else ""))
            self.functions[fn.name] = (fn.return_type, [t for t, _ in fn.params])
        if "main" not in self.functions:
            self.errors.append("no 'main' function defined")
        for fn in program.functions:
            try:
                self._check_function(fn)
            except SemanticError as e:
                self.errors.append(str(e))
        return self.errors

    def _check_function(self, fn: FunctionDecl) -> None:
        if fn.return_type not in ("int", "float"):
            raise SemanticError(f"unsupported return type '{fn.return_type}'")
        scope = Scope()
        for ptype, pname in fn.params:
            scope.declare(pname, ptype)
        self._current_return = fn.return_type
        self._check_block(fn.body, scope)

    def _check_block(self, block: Block, scope: Scope) -> None:
        for stmt in block.statements:
            self._check_stmt(stmt, scope)

    def _check_stmt(self, stmt, scope: Scope) -> None:
        if isinstance(stmt, VarDecl):
            if stmt.init_expr:
                self._type_of_expr(stmt.init_expr, scope)
            scope.declare(stmt.name, stmt.var_type)
        elif isinstance(stmt, AssignStmt):
            found = scope.lookup(stmt.name)
            if found is None:
                loc = getattr(stmt, "loc", None)
                raise SemanticError(f"assignment to undeclared variable '{stmt.name}'",
                                    line=loc[0] if loc else None)
            self._type_of_expr(stmt.expr, scope)
        elif isinstance(stmt, ReturnStmt):
            t = self._type_of_expr(stmt.expr, scope)
            if self._current_return == "int" and t == "float":
                raise SemanticError("returning float from int function (implicit narrowing)")
        elif isinstance(stmt, IfStmt):
            ct = self._type_of_expr(stmt.condition, scope)
            if not _numeric(ct):
                raise SemanticError("if condition must be numeric")
            self._check_block(stmt.then_branch, Scope(scope))
            if stmt.else_branch:
                self._check_block(stmt.else_branch, Scope(scope))
        elif isinstance(stmt, Block):
            self._check_block(stmt, Scope(scope))
        elif isinstance(stmt, WhileStmt):
            ct = self._type_of_expr(stmt.condition, scope)
            if not _numeric(ct):
                raise SemanticError("while condition must be numeric")
            self._check_block(stmt.body, Scope(scope))
        else:
            raise SemanticError(f"unknown statement node {type(stmt).__name__}")

    def _type_of_expr(self, expr, scope: Scope) -> str:
        if isinstance(expr, Number):
            return "float" if expr.is_float else "int"
        if isinstance(expr, Identifier):
            t = scope.lookup(expr.name)
            if t is None:
                loc = getattr(expr, "loc", None)
                raise SemanticError(f"use of undeclared variable '{expr.name}'",
                                    line=loc[0] if loc else None)
            return t
        if isinstance(expr, UnaryOp):
            t = self._type_of_expr(expr.operand, scope)
            if not _numeric(t):
                raise SemanticError(f"unary '{expr.op}' requires a numeric operand")
            return "int" if expr.op == "NOT" else t
        if isinstance(expr, BinaryOp):
            lt = self._type_of_expr(expr.left, scope)
            rt = self._type_of_expr(expr.right, scope)
            if not (_numeric(lt) and _numeric(rt)):
                raise SemanticError(f"operator '{expr.op}' requires numeric operands")
            if expr.op == "MOD" and (lt == "float" or rt == "float"):
                raise SemanticError("operator '%' requires integer operands")
            if expr.op in ("EQ", "NEQ", "LT", "GT", "LE", "GE"):
                return "int"  # comparison result
            if lt == "float" or rt == "float":
                return "float"
            return "int"
        if isinstance(expr, CallExpr):
            sig = self.functions.get(expr.name)
            if sig is None:
                raise SemanticError(f"call to undeclared function '{expr.name}'")
            ret_type, ptypes = sig
            if len(expr.args) != len(ptypes):
                raise SemanticError(
                    f"function '{expr.name}' expects {len(ptypes)} argument(s), got {len(expr.args)}")
            for arg in expr.args:
                at = self._type_of_expr(arg, scope)
                if not _numeric(at):
                    raise SemanticError(f"argument to '{expr.name}' must be numeric")
            return ret_type
        raise SemanticError(f"unknown expression node {type(expr).__name__}")
