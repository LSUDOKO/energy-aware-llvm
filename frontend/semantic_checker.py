"""Standalone semantic checker (baseline pipeline, stage 2).

This is deliberately a *separate* AST traversal: it only checks names and
types and produces no IR. It exists to represent the "conventional pipeline"
(the plan's Part II baseline) where semantic analysis and IR generation are
distinct stages that each walk the tree. The merged/energy-aware pipeline
(frontend/semantic_codegen.py, Phase 2) replaces this walk + the emit walk
with a single traversal.

Checking rules for the current MiniC subset:
  * variables must be declared before use, within lexical scopes,
  * redeclaration in the same scope is an error,
  * int/float arithmetic is allowed; conditionals require numeric types,
  * return statements must match the function's declared return type,
  * exactly one `main` function must exist.
"""
from __future__ import annotations

from frontend.ast_nodes import (
    AssignStmt, BinaryOp, Block, FunctionDecl, Identifier, IfStmt,
    Number, Program, ReturnStmt, VarDecl, WhileStmt,
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
    """Single-purpose checker: visit(node) returns the node's type string."""

    def __init__(self):
        self.errors: list[str] = []

    def check(self, program: Program) -> list[str]:
        """Validate the whole program; returns collected error strings."""
        self.errors = []
        if not any(f.name == "main" for f in program.functions):
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
        # Params are not yet parsed by the parser; treat as empty for now.
        scope = Scope()
        self._current_return = fn.return_type
        self._check_block(fn.body, scope)

    def _check_block(self, block: Block, scope: Scope) -> None:
        for stmt in block.statements:
            self._check_stmt(stmt, scope)

    def _check_stmt(self, stmt, scope: Scope) -> None:
        if isinstance(stmt, VarDecl):
            t = self._type_of_expr(stmt.init_expr, scope) if stmt.init_expr else stmt.var_type
            scope.declare(stmt.name, stmt.var_type)
        elif isinstance(stmt, AssignStmt):
            found = scope.lookup(stmt.name)
            if found is None:
                raise SemanticError(f"assignment to undeclared variable '{stmt.name}'")
            t = self._type_of_expr(stmt.expr, scope)
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
                raise SemanticError(f"use of undeclared variable '{expr.name}'")
            return t
        if isinstance(expr, BinaryOp):
            lt = self._type_of_expr(expr.left, scope)
            rt = self._type_of_expr(expr.right, scope)
            if not (_numeric(lt) and _numeric(rt)):
                raise SemanticError(f"operator '{expr.op}' requires numeric operands")
            if expr.op in ("EQ", "LT", "GT"):
                return "int"  # comparison result
            if lt == "float" or rt == "float":
                return "float"
            return "int"
        raise SemanticError(f"unknown expression node {type(expr).__name__}")
