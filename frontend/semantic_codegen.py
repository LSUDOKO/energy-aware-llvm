"""Unified Semantic Visitor (plan Part II, stages 1-6).

Single traversal that resolves names, checks types, tracks effects and
constant facts, and emits SSA-friendly LLVM IR — replacing the conventional
"check walk + emit walk" pair with one pass plus online local optimization
during emission.

Key mechanisms:

  * single traversal: every visit resolves names/types and emits IR only
    when the construct is valid (stage 3)
  * scoped symbol table with per-variable constant facts (stage 3);
    assignment re-propagates constants, loops/branches invalidate them
    conservatively
  * pure constant evaluator decides branch/loop pruning *before* any IR is
    emitted (stage 4 + stage 6 unreachable-code removal)
  * all arithmetic flows through SimplifyingIRBuilder (stage 4)
  * effects are tracked on every TypedValue (stage 5)
  * diagnostics carry source locations and recovery skips to the next
    statement, so one bad construct does not abort the function

Lowering contract (stage 3): ``_lower_expression(node) -> TypedValue`` where
TypedValue carries the resolved type, the LLVM value, an optional compile-time
constant, and an effects set. AST nodes are annotated with type/const_value
as they are lowered (the plan's "common typed representation").
"""
from __future__ import annotations

from dataclasses import dataclass, field

from llvmlite import binding, ir

from frontend.ast_nodes import (
    AssignStmt, BinaryOp, Block, CallExpr, FunctionDecl, Identifier, IfStmt,
    Number, Program, ReturnStmt, UnaryOp, VarDecl, WhileStmt,
)
from frontend.ir_builder import SimplifyingIRBuilder, _is_const
from frontend.numeric import c_div, f32, wrap_int


class CodeGenError(Exception):
    pass


@dataclass
class TypedValue:
    type: ir.Type
    value: ir.Value
    const: object | None = None
    is_float: bool = False
    effects: frozenset = frozenset()


@dataclass
class Symbol:
    alloca: ir.Instruction
    is_float: bool
    const: object | None = None  # compile-time value, None = runtime


@dataclass
class Artifacts:
    module: ir.Module
    diagnostics: list
    stats: dict


STAT_KEYS = (
    "folded", "identities", "strength_reductions", "loads_eliminated",
    "loads_emitted", "stores_emitted", "branches_pruned", "loops_pruned",
    "statements_pruned",
)


def _const_of(value):
    return value.constant if isinstance(value, ir.Constant) else None


def _is_i1(value) -> bool:
    return isinstance(value.type, ir.IntType) and value.type.width == 1


def _assigned_names(node) -> set:
    """Names assigned anywhere in this statement subtree (conservative)."""
    names: set = set()

    def walk(n):
        if isinstance(n, AssignStmt):
            names.add(n.name)
        elif isinstance(n, VarDecl):
            names.add(n.name)
        elif isinstance(n, Block):
            for s in n.statements:
                walk(s)
        elif isinstance(n, IfStmt):
            walk(n.then_branch)
            if n.else_branch is not None:
                walk(n.else_branch)
        elif isinstance(n, WhileStmt):
            walk(n.body)

    walk(node)
    return names


class UnifiedSemanticVisitor:
    """Single-pass semantic analysis + typed IR emission + online folding."""

    def __init__(self):
        binding.initialize_native_target()
        binding.initialize_native_asmprinter()

        self.module = ir.Module(name="minic_module")
        self.module.triple = binding.get_default_triple()
        target = binding.Target.from_default_triple()
        self.module.data_layout = target.create_target_machine().target_data

        self.i32 = ir.IntType(32)
        self.f32 = ir.FloatType()
        self.i1 = ir.IntType(1)

        self.builder: ir.IRBuilder | None = None
        self.sb: SimplifyingIRBuilder | None = None
        self.scopes: list[dict[str, Symbol]] = []
        self.functions: dict[str, ir.Function] = {}
        self.diagnostics: list[str] = []
        self.current_return: ir.Type = self.i32
        self.stats: dict[str, int] = {k: 0 for k in STAT_KEYS}

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    def generate(self, program: Program) -> Artifacts:
        for fn in program.functions:
            self._declare_function(fn)
        for fn in program.functions:
            try:
                self._lower_function(fn)
            except CodeGenError:
                pass  # diagnostic already recorded; recover at function level
        return Artifacts(self.module, self.diagnostics, dict(self.stats))

    def _declare_function(self, fn: FunctionDecl) -> None:
        if fn.name in self.functions:
            line = fn.loc[0] if fn.loc else "?"
            self.diagnostics.append(f"line {line}: duplicate definition of function '{fn.name}'")
            return
        ret = self.f32 if fn.return_type == "float" else self.i32
        ptypes = [self.f32 if t == "float" else self.i32 for t, _ in fn.params]
        self.functions[fn.name] = ir.Function(
            self.module, ir.FunctionType(ret, ptypes), name=fn.name)

    # ------------------------------------------------------------------
    # Function / block / statement lowering
    # ------------------------------------------------------------------

    def _lower_function(self, fn: FunctionDecl) -> None:
        func = self.functions.get(fn.name)
        if func is None:
            return
        block = func.append_basic_block("entry")
        self.builder = ir.IRBuilder(block)
        self.sb = SimplifyingIRBuilder(self.builder, self.i32, self.f32)
        self.scopes = [{}]
        self.current_return = func.function_type.return_type

        # Bind parameters as runtime (non-constant) locals.
        for (ptype, pname), arg in zip(fn.params, func.args):
            arg.name = pname
            alloca = self.builder.alloca(
                self.f32 if ptype == "float" else self.i32, name=pname)
            self.builder.store(arg, alloca)
            self._declare(pname, Symbol(alloca, ptype == "float"), fn)

        self._lower_block(fn.body)

        if not self.builder.block.is_terminated:
            self.builder.ret(ir.Constant(self.current_return, 0))

        # Accumulate this function's builder counters.
        self.stats["folded"] += self.sb.folded
        self.stats["identities"] += self.sb.identities
        self.stats["strength_reductions"] += self.sb.strength_reductions

    def _lower_block(self, block: Block) -> None:
        self.scopes.append({})
        try:
            for stmt in block.statements:
                self._lower_statement(stmt)
        finally:
            self.scopes.pop()

    def _lower_statement(self, stmt) -> None:
        if self.builder.block.is_terminated:
            # Unreachable-code elimination (stage 6): statements after a
            # terminator in the same block are dead.
            self.stats["statements_pruned"] += 1
            return
        handler = getattr(self, f"_stmt_{type(stmt).__name__}", None)
        if handler is None:
            self._err(stmt, f"unsupported statement {type(stmt).__name__}")
        try:
            handler(stmt)
        except CodeGenError:
            pass  # diagnostic already recorded; recover at statement boundary

    def _stmt_Block(self, block: Block) -> None:
        self._lower_block(block)

    def _stmt_VarDecl(self, node: VarDecl) -> None:
        is_float = node.var_type == "float"
        alloca = self.builder.alloca(self.f32 if is_float else self.i32, name=node.name)
        const = None
        if node.init_expr is not None:
            tv = self._lower_expression(node.init_expr)
            tv = self._coerce(tv, is_float)
            self.builder.store(tv.value, alloca)
            self.stats["stores_emitted"] += 1
            const = tv.const
        self._declare(node.name, Symbol(alloca, is_float, const), node)

    def _stmt_AssignStmt(self, node: AssignStmt) -> None:
        sym = self._lookup(node.name, node)
        tv = self._lower_expression(node.expr)
        tv = self._coerce(tv, sym.is_float)
        self.builder.store(tv.value, sym.alloca)
        self.stats["stores_emitted"] += 1
        # Constant propagation through assignment.
        sym.const = tv.const

    def _stmt_ReturnStmt(self, node: ReturnStmt) -> None:
        tv = self._lower_expression(node.expr)
        tv = self._coerce(tv, isinstance(self.current_return, ir.FloatType))
        self.builder.ret(tv.value)

    def _stmt_IfStmt(self, node: IfStmt) -> None:
        cond_const = self._const_eval(node.condition)
        if cond_const is not None:
            # Constant condition: emit only the taken path, no branch IR.
            self.stats["branches_pruned"] += 1
            if cond_const != 0:
                self._lower_statement(node.then_branch)
            elif node.else_branch is not None:
                self._lower_statement(node.else_branch)
            return

        # Conservative invalidation before lowering either branch so no
        # branch observes the other's constant facts.
        self._invalidate_assigned(node.then_branch)
        if node.else_branch is not None:
            self._invalidate_assigned(node.else_branch)

        cond_i1 = self._as_i1(self._lower_expression(node.condition))
        then_bb = self.builder.append_basic_block("then")
        merge_bb = self.builder.append_basic_block("ifcont")
        if node.else_branch is not None:
            else_bb = self.builder.append_basic_block("else")
            self.builder.cbranch(cond_i1, then_bb, else_bb)
            self.builder.position_at_end(then_bb)
            self._lower_statement(node.then_branch)
            if not self.builder.block.is_terminated:
                self.builder.branch(merge_bb)
            self.builder.position_at_end(else_bb)
            self._lower_statement(node.else_branch)
            if not self.builder.block.is_terminated:
                self.builder.branch(merge_bb)
        else:
            self.builder.cbranch(cond_i1, then_bb, merge_bb)
            self.builder.position_at_end(then_bb)
            self._lower_statement(node.then_branch)
            if not self.builder.block.is_terminated:
                self.builder.branch(merge_bb)
        self.builder.position_at_end(merge_bb)

    def _stmt_WhileStmt(self, node: WhileStmt) -> None:
        # The body may run any number of times (including zero): invalidate
        # body-assigned variables BEFORE the condition so it is evaluated at
        # runtime whenever the loop is data-dependent.
        self._invalidate_assigned(node.body)

        cond_const = self._const_eval(node.condition)
        if cond_const is not None and cond_const == 0:
            self.stats["loops_pruned"] += 1
            return  # while(0): drop the body entirely

        if cond_const is not None:
            # while(non-zero): loop with unconditional back edge.
            self.stats["branches_pruned"] += 1
            loop_bb = self.builder.append_basic_block("whileloop")
            self.builder.branch(loop_bb)
            self.builder.position_at_end(loop_bb)
            self._lower_statement(node.body)
            if not self.builder.block.is_terminated:
                self.builder.branch(loop_bb)
            return

        cond_bb = self.builder.append_basic_block("whilecond")
        loop_bb = self.builder.append_basic_block("whileloop")
        after_bb = self.builder.append_basic_block("whileafter")
        self.builder.branch(cond_bb)
        self.builder.position_at_end(cond_bb)
        cond_i1 = self._as_i1(self._lower_expression(node.condition))
        self.builder.cbranch(cond_i1, loop_bb, after_bb)

        self.builder.position_at_end(loop_bb)
        self._lower_statement(node.body)
        if not self.builder.block.is_terminated:
            self.builder.branch(cond_bb)

        self.builder.position_at_end(after_bb)

    # ------------------------------------------------------------------
    # Expression lowering
    # ------------------------------------------------------------------

    def _lower_expression(self, node) -> TypedValue:
        handler = getattr(self, f"_expr_{type(node).__name__}", None)
        if handler is None:
            self._err(node, f"unsupported expression {type(node).__name__}")
        tv = handler(node)
        # Annotate the AST node: the common typed representation (stage 1).
        node.type = "float" if tv.is_float else str(tv.type)
        node.const_value = tv.const
        node.value_category = "rvalue"
        return tv

    def _expr_Number(self, node: Number) -> TypedValue:
        if node.is_float:
            value = f32(float(node.value))     # the literal as binary32
            return TypedValue(self.f32, ir.Constant(self.f32, value),
                              const=value, is_float=True)
        value = wrap_int(int(node.value))
        return TypedValue(self.i32, ir.Constant(self.i32, value), const=value)

    def _expr_Identifier(self, node: Identifier) -> TypedValue:
        sym = self._lookup(node.name, node)
        if sym.const is not None:
            # Incremental data-flow fact: no load needed (stage 4).
            self.stats["loads_eliminated"] += 1
            if sym.is_float:
                return TypedValue(self.f32, ir.Constant(self.f32, float(sym.const)),
                                  const=sym.const, is_float=True)
            return TypedValue(self.i32, ir.Constant(self.i32, int(sym.const)),
                              const=sym.const)
        value = self.builder.load(sym.alloca, node.name)
        self.stats["loads_emitted"] += 1
        return TypedValue(value.type, value, is_float=sym.is_float,
                          effects=frozenset({f"read:{node.name}"}))

    def _expr_UnaryOp(self, node: UnaryOp) -> TypedValue:
        operand = self._lower_expression(node.operand)
        if node.op == "MINUS":
            value = self.sb.neg(operand.value)
            return TypedValue(value.type, value, const=_const_of(value),
                              is_float=operand.is_float)
        if node.op == "NOT":
            value = self.sb.logical_not(operand.value)
            return TypedValue(self.i1, value, const=_const_of(value))
        self._err(node, f"unknown unary operator {node.op}")

    def _expr_BinaryOp(self, node: BinaryOp) -> TypedValue:
        op = node.op
        left = self._lower_expression(node.left)
        right = self._lower_expression(node.right)

        if op in ("EQ", "NEQ", "LT", "GT", "LE", "GE"):
            if left.is_float or right.is_float:
                lv = self._coerce(left, True).value
                rv = self._coerce(right, True).value
                value = self.sb.fcmp(op, lv, rv)
            else:
                lv = self._coerce(left, False).value
                rv = self._coerce(right, False).value
                value = self.sb.icmp(op, lv, rv)
            return TypedValue(self.i1, value, const=_const_of(value))

        if left.is_float or right.is_float:
            lv = self._coerce(left, True).value
            rv = self._coerce(right, True).value
            value = self.sb.arith(op, lv, rv)
            return TypedValue(self.f32, value, const=_const_of(value), is_float=True)

        lv = self._coerce(left, False).value
        rv = self._coerce(right, False).value
        value = self.sb.arith(op, lv, rv)
        return TypedValue(self.i32, value, const=_const_of(value))

    def _expr_CallExpr(self, node: CallExpr) -> TypedValue:
        func = self.functions.get(node.name)
        if func is None:
            self._err(node, f"call to undeclared function '{node.name}'")
        ftype = func.function_type
        if len(node.args) != len(ftype.args):
            self._err(node, f"function '{node.name}' expects {len(ftype.args)} "
                            f"argument(s), got {len(node.args)}")
        args = []
        for arg_node, param_type in zip(node.args, ftype.args):
            tv = self._lower_expression(arg_node)
            tv = self._coerce(tv, isinstance(param_type, ir.FloatType))
            args.append(tv.value)
        call = self.builder.call(func, args, "calltmp")
        return TypedValue(ftype.return_type, call,
                          is_float=isinstance(ftype.return_type, ir.FloatType))

    # ------------------------------------------------------------------
    # Environment / coercions / pruning helpers
    # ------------------------------------------------------------------

    def _lookup(self, name: str, node) -> Symbol:
        for scope in reversed(self.scopes):
            if name in scope:
                return scope[name]
        self._err(node, f"use of undeclared variable '{name}'")

    def _declare(self, name: str, symbol: Symbol, node) -> None:
        scope = self.scopes[-1]
        if name in scope:
            self._err(node, f"redeclaration of variable '{name}' in same scope")
        scope[name] = symbol

    def _invalidate_assigned(self, node) -> None:
        """Drop constant facts for variables assigned in this subtree."""
        for name in _assigned_names(node):
            for scope in reversed(self.scopes):
                if name in scope:
                    scope[name].const = None
                    break

    def _coerce(self, tv: TypedValue, to_float: bool) -> TypedValue:
        if to_float:
            if tv.is_float:
                return tv
            value = self.sb.to_float(tv.value)
            return TypedValue(self.f32, value, const=_const_of(value), is_float=True)
        if not tv.is_float and not _is_i1(tv.value):
            return tv
        value = self.sb.to_int(tv.value)
        return TypedValue(self.i32, value, const=_const_of(value))

    def _as_i1(self, tv: TypedValue):
        """Numeric truthiness for conditions (non-zero is true)."""
        value = tv.value
        if _is_i1(value):
            return value
        if tv.is_float:
            return self.sb.fcmp('NEQ', value, ir.Constant(self.f32, 0.0), "condtmp")
        return self.sb.icmp('NEQ', value, ir.Constant(self.i32, 0), "condtmp")

    def _const_eval(self, node):
        """Pure AST-level constant evaluation (no IR emission).

        Used to decide branch/loop pruning before lowering. Conservative:
        returns None unless every operand is a known compile-time constant.
        """
        if isinstance(node, Number):
            return f32(float(node.value)) if node.is_float else wrap_int(int(node.value))
        if isinstance(node, Identifier):
            for scope in reversed(self.scopes):
                if node.name in scope:
                    return scope[node.name].const
            return None  # unknown name; normal path will diagnose
        if isinstance(node, UnaryOp):
            v = self._const_eval(node.operand)
            if v is None:
                return None
            if node.op == "MINUS":
                return -v if isinstance(v, float) else wrap_int(-v)
            if node.op == "NOT":
                return 1 if v == 0 else 0
            return None
        if isinstance(node, BinaryOp):
            lv = self._const_eval(node.left)
            rv = self._const_eval(node.right)
            if lv is None or rv is None:
                return None
            op = node.op
            if op in ("EQ", "NEQ", "LT", "GT", "LE", "GE"):
                return 1 if {
                    "EQ": lv == rv, "NEQ": lv != rv, "LT": lv < rv,
                    "GT": lv > rv, "LE": lv <= rv, "GE": lv >= rv,
                }[op] else 0
            if isinstance(lv, float) or isinstance(rv, float):
                lv, rv = f32(float(lv)), f32(float(rv))   # binary32 arithmetic
                if op == "PLUS": return f32(lv + rv)
                if op == "MINUS": return f32(lv - rv)
                if op == "MUL": return f32(lv * rv)
                if op == "DIV":
                    return None if rv == 0 else f32(lv / rv)
                return None
            if op == "PLUS": return wrap_int(lv + rv)
            if op == "MINUS": return wrap_int(lv - rv)
            if op == "MUL": return wrap_int(lv * rv)
            if op == "DIV":
                if rv == 0:
                    return None  # runtime trap; don't prune on it
                return wrap_int(c_div(lv, rv))
            return None
        return None  # calls and anything else: runtime

    def _err(self, node, message: str) -> None:
        loc = getattr(node, "loc", None)
        where = f"line {loc[0]}" if loc else "?"
        text = f"{where}: {message}"
        self.diagnostics.append(text)
        raise CodeGenError(text)
