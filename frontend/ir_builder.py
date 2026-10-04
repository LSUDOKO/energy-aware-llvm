"""Simplifying IR builder (plan Part II, stage 4: "optimize during emission").

Wraps llvmlite's IRBuilder and applies transformations that need only local
facts *before* an instruction is allocated:

  * constant folding            - both operands constant -> compute in Python
  * algebraic identities        - x+0, 0+x, x-0, x*1, 1*x, x*0, 0*x, x/1
  * strength reduction          - x * 2^k -> shl
  * lazy cast elision           - to_int/to_float no-op or fold when possible

Every avoided or cheapened instruction is counted (``folded``, ``identities``,
``strength_reductions``) so experiments can quantify eliminated work. C
semantics are preserved: signed division truncates toward zero and division
by zero is never folded (it stays a runtime trap).
"""
from __future__ import annotations

from llvmlite import ir


def _is_const(v) -> bool:
    return isinstance(v, ir.Constant)


def _const_val(v):
    return v.constant if isinstance(v, ir.Constant) else None


def _is_int_const(v, n) -> bool:
    return (isinstance(v, ir.Constant) and isinstance(v.type, ir.IntType)
            and v.constant == n)


def _is_float_const(v, n: float) -> bool:
    return (isinstance(v, ir.Constant) and isinstance(v.type, ir.FloatType)
            and v.constant == n)


def _is_zero(v) -> bool:
    if not isinstance(v, ir.Constant):
        return False
    if isinstance(v.type, ir.IntType):
        return v.constant == 0
    if isinstance(v.type, ir.FloatType):
        return v.constant == 0.0
    return False


def _is_one(v) -> bool:
    if not isinstance(v, ir.Constant):
        return False
    if isinstance(v.type, ir.IntType):
        return v.constant == 1
    if isinstance(v.type, ir.FloatType):
        return v.constant == 1.0
    return False


def _is_pow2(n) -> bool:
    return isinstance(n, int) and n > 0 and (n & (n - 1)) == 0


def _c_div(a: int, b: int) -> int:
    """C signed division: truncation toward zero (Python // floors)."""
    q = abs(a) // abs(b)
    return q if (a >= 0) == (b >= 0) else -q


CMP_PREDICATES = {'EQ': '==', 'NEQ': '!=', 'LT': '<', 'GT': '>', 'LE': '<=', 'GE': '>='}


class SimplifyingIRBuilder:
    def __init__(self, builder: ir.IRBuilder, i32: ir.IntType, f32: ir.FloatType):
        self.b = builder
        self.i32 = i32
        self.f32 = f32
        self.folded = 0              # instructions avoided entirely
        self.identities = 0          # instructions replaced by an operand
        self.strength_reductions = 0  # instructions replaced by cheaper ones

    # ------------------------------------------------------------------
    # Integer arithmetic
    # ------------------------------------------------------------------

    def add(self, a, b, name="addtmp"):
        if _is_const(a) and _is_const(b):
            self.folded += 1
            return ir.Constant(a.type, a.constant + b.constant)
        if _is_zero(b):
            self.identities += 1
            return a
        if _is_zero(a):
            self.identities += 1
            return b
        return self.b.add(a, b, name)

    def sub(self, a, b, name="subtmp"):
        if _is_const(a) and _is_const(b):
            self.folded += 1
            return ir.Constant(a.type, a.constant - b.constant)
        if _is_zero(b):
            self.identities += 1
            return a
        return self.b.sub(a, b, name)

    def mul(self, a, b, name="multmp"):
        if _is_const(a) and _is_const(b):
            self.folded += 1
            return ir.Constant(a.type, a.constant * b.constant)
        if _is_one(a):
            self.identities += 1
            return b
        if _is_one(b):
            self.identities += 1
            return a
        if _is_zero(a):
            self.identities += 1
            return ir.Constant(b.type, 0 if isinstance(b.type, ir.IntType) else 0.0)
        if _is_zero(b):
            self.identities += 1
            return ir.Constant(a.type, 0 if isinstance(a.type, ir.IntType) else 0.0)
        # Strength reduction: x * 2^k -> shl x, k (two's complement safe)
        for factor, other in ((a, b), (b, a)):
            if (isinstance(factor, ir.Constant) and isinstance(factor.type, ir.IntType)
                    and _is_pow2(factor.constant) and not _is_const(other)):
                self.strength_reductions += 1
                shift = ir.Constant(factor.type, factor.constant.bit_length() - 1)
                return self.b.shl(other, shift, "shifttmp")
        return self.b.mul(a, b, name)

    def sdiv(self, a, b, name="divtmp"):
        if _is_const(a) and _is_const(b):
            if b.constant != 0:
                self.folded += 1
                return ir.Constant(a.type, _c_div(a.constant, b.constant))
            # division by zero: leave as a runtime trap
            return self.b.sdiv(a, b, name)
        if _is_one(b):
            self.identities += 1
            return a
        return self.b.sdiv(a, b, name)

    # ------------------------------------------------------------------
    # Float arithmetic
    # ------------------------------------------------------------------

    def fadd(self, a, b, name="faddtmp"):
        if _is_const(a) and _is_const(b):
            self.folded += 1
            return ir.Constant(a.type, a.constant + b.constant)
        if _is_zero(b):
            self.identities += 1
            return a
        if _is_zero(a):
            self.identities += 1
            return b
        return self.b.fadd(a, b, name)

    def fsub(self, a, b, name="fsubtmp"):
        if _is_const(a) and _is_const(b):
            self.folded += 1
            return ir.Constant(a.type, a.constant - b.constant)
        if _is_zero(b):
            self.identities += 1
            return a
        return self.b.fsub(a, b, name)

    def fmul(self, a, b, name="fmultmp"):
        if _is_const(a) and _is_const(b):
            self.folded += 1
            return ir.Constant(a.type, a.constant * b.constant)
        if _is_one(a):
            self.identities += 1
            return b
        if _is_one(b):
            self.identities += 1
            return a
        if _is_zero(a) or _is_zero(b):
            self.identities += 1
            other = b if _is_zero(a) else a
            return ir.Constant(other.type, 0.0)
        return self.b.fmul(a, b, name)

    def fdiv(self, a, b, name="fdivtmp"):
        if _is_const(a) and _is_const(b) and b.constant != 0.0:
            self.folded += 1
            return ir.Constant(a.type, a.constant / b.constant)
        if _is_one(b):
            self.identities += 1
            return a
        return self.b.fdiv(a, b, name)

    # ------------------------------------------------------------------
    # Dispatch by operator token
    # ------------------------------------------------------------------

    def arith(self, op: str, a, b, name=None):
        if op == 'PLUS':
            return self.fadd(a, b) if isinstance(a.type, ir.FloatType) else self.add(a, b)
        if op == 'MINUS':
            return self.fsub(a, b) if isinstance(a.type, ir.FloatType) else self.sub(a, b)
        if op == 'MUL':
            return self.fmul(a, b) if isinstance(a.type, ir.FloatType) else self.mul(a, b)
        if op == 'DIV':
            return self.fdiv(a, b) if isinstance(a.type, ir.FloatType) else self.sdiv(a, b)
        raise ValueError(f"unknown arithmetic operator {op}")

    # ------------------------------------------------------------------
    # Comparisons (return i1, or a constant i1 when folded)
    # ------------------------------------------------------------------

    def icmp(self, op: str, a, b, name="cmptmp"):
        pred = CMP_PREDICATES[op]
        if _is_const(a) and _is_const(b):
            self.folded += 1
            truth = {
                '==': a.constant == b.constant,
                '!=': a.constant != b.constant,
                '<': a.constant < b.constant,
                '>': a.constant > b.constant,
                '<=': a.constant <= b.constant,
                '>=': a.constant >= b.constant,
            }[pred]
            return ir.Constant(ir.IntType(1), 1 if truth else 0)
        return self.b.icmp_signed(pred, a, b, name)

    def fcmp(self, op: str, a, b, name="cmptmp"):
        pred = CMP_PREDICATES[op]
        if _is_const(a) and _is_const(b):
            self.folded += 1
            truth = {
                '==': a.constant == b.constant,
                '!=': a.constant != b.constant,
                '<': a.constant < b.constant,
                '>': a.constant > b.constant,
                '<=': a.constant <= b.constant,
                '>=': a.constant >= b.constant,
            }[pred]
            return ir.Constant(ir.IntType(1), 1 if truth else 0)
        return self.b.fcmp_ordered(pred, a, b, name)

    # ------------------------------------------------------------------
    # Unary
    # ------------------------------------------------------------------

    def neg(self, a, name="negtmp"):
        if _is_const(a):
            self.folded += 1
            return ir.Constant(a.type, -a.constant)
        if isinstance(a.type, ir.FloatType):
            return self.b.fsub(ir.Constant(a.type, 0.0), a, name)
        return self.b.neg(a, name)

    def logical_not(self, a, name="nottmp"):
        if _is_const(a):
            self.folded += 1
            return ir.Constant(ir.IntType(1), 1 if a.constant == 0 else 0)
        if isinstance(a.type, ir.FloatType):
            return self.b.fcmp_ordered('==', a, ir.Constant(a.type, 0.0), name)
        return self.b.icmp_signed('==', a, ir.Constant(a.type, 0), name)

    # ------------------------------------------------------------------
    # Lazy casts (redundant cast removal)
    # ------------------------------------------------------------------

    def to_int(self, value):
        """Coerce to i32, folding constant conversions."""
        if isinstance(value.type, ir.IntType):
            if value.type.width == self.i32.width:
                return value
            if value.type.width == 1:
                if _is_const(value):
                    self.folded += 1
                    return ir.Constant(self.i32, int(value.constant))
                return self.b.zext(value, self.i32, "zexttmp")
        if isinstance(value.type, ir.FloatType):
            if _is_const(value):
                self.folded += 1
                return ir.Constant(self.i32, int(value.constant))  # C truncation
            return self.b.fptosi(value, self.i32, "trunctmp")
        raise TypeError(f"cannot convert {value.type} to i32")

    def to_float(self, value):
        """Coerce to f32, folding constant conversions."""
        if isinstance(value.type, ir.FloatType):
            return value
        if _is_const(value):
            self.folded += 1
            return ir.Constant(self.f32, float(value.constant))
        if isinstance(value.type, ir.IntType) and value.type.width == 1:
            return self.b.zext(value, self.i32, "zexttmp")
        return self.b.sitofp(value, self.f32, "sitofptmp")
