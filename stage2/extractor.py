"""Stage 2 — static IR feature extraction (plan: "35+ IR Metrics Extractor").

Rewritten from string parsing to a real walk over LLVM IR using llvmlite's
``binding`` API: module -> functions -> basic blocks -> instructions ->
operands.  Loop metrics come from an actual dominator computation over the
CFG (back edges -> natural loops -> nesting depth), not heuristics.

The extractor accepts any of:

  * ``str``            — LLVM IR text
  * ``ir.Module``      — llvmlite IR-builder module (frontend output)
  * ``binding.ModuleRef`` — parsed module

and returns a plain ``dict`` of 40+ numeric metrics.  The historical 13
keys from the original string-based extractor are kept as aliases so
existing consumers (web UI, ML feature vectors) keep working.
"""
from __future__ import annotations

from llvmlite import binding, ir

# ---------------------------------------------------------------------------
# opcode families
# ---------------------------------------------------------------------------

INT_ARITH = {"add", "sub", "mul", "udiv", "sdiv", "urem", "srem"}
FP_ARITH = {"fadd", "fsub", "fmul", "fdiv", "frem"}
BITWISE = {"and", "or", "xor", "shl", "lshr", "ashr"}
CASTS = {
    "trunc", "zext", "sext", "fptrunc", "fpext", "fptoui", "fptosi",
    "uitofp", "sitofp", "ptrtoint", "inttoptr", "bitcast", "addrspacecast",
}
TERMINATORS = {"ret", "br", "switch", "indirectbr", "invoke", "unreachable"}
MEMORY = {"load", "store", "alloca", "getelementptr", "atomicrmw", "cmpxchg"}

BK_ALU = "num_alu_ops"
BK_FPOP = "num_fp_ops"
BK_MEM = "num_allocas"


def _is_int_type(t) -> bool:
    return getattr(t, "type_kind", None) == binding.TypeKind.integer


def _is_float_type(t) -> bool:
    return getattr(t, "type_kind", None) in (
        binding.TypeKind.float, binding.TypeKind.double,
        binding.TypeKind.half, binding.TypeKind.x86_fp80,
        binding.TypeKind.fp128, binding.TypeKind.bfloat,
    )


class _CFG:
    """Control-flow graph + dominators + natural loops for one function."""

    def __init__(self, blocks: list):
        self.blocks = list(blocks)
        self.names = [b.name for b in self.blocks]
        self.index = {b.name: i for i, b in enumerate(self.blocks)}
        self.succ: list[list[int]] = [[] for _ in self.blocks]
        self.pred: list[list[int]] = [[] for _ in self.blocks]
        self._build()

    def _build(self) -> None:
        for i, blk in enumerate(self.blocks):
            term = None
            for ins in blk.instructions:
                if ins.opcode in TERMINATORS:
                    term = ins
            if term is None:
                continue
            labels = [
                o for o in term.operands
                if o.value_kind == binding.ValueKind.basic_block
            ]
            for o in labels:
                j = self.index.get(o.name)
                if j is None or j == i:
                    continue
                if j not in self.succ[i]:
                    self.succ[i].append(j)
                    self.pred[j].append(i)

    # -- dominators ---------------------------------------------------------
    def dominators(self) -> list[set]:
        n = len(self.blocks)
        if n == 0:
            return []
        all_nodes = set(range(n))
        dom = [set(all_nodes) for _ in range(n)]
        dom[0] = {0}
        changed = True
        while changed:
            changed = False
            for i in range(1, n):
                preds = self.pred[i]
                if not preds:
                    new = {i}
                else:
                    new = set(dom[preds[0]])
                    for p in preds[1:]:
                        new &= dom[p]
                new = new | {i}
                if new != dom[i]:
                    dom[i] = new
                    changed = True
        return dom

    # -- loops ---------------------------------------------------------------
    def back_edges(self, dom: list[set]) -> list[tuple[int, int]]:
        """(src, header) edges where header dominates src."""
        out = []
        for i in range(len(self.blocks)):
            for j in self.succ[i]:
                if j in dom[i]:
                    out.append((i, j))
        return out

    def natural_loops(self, dom: list[set]) -> list[dict]:
        loops = []
        for src, hdr in self.back_edges(dom):
            nodes = {hdr}
            stack = []
            if src != hdr:
                nodes.add(src)
                stack.append(src)
            while stack:
                n = stack.pop()
                for p in self.pred[n]:
                    if p not in nodes:
                        nodes.add(p)
                        stack.append(p)
            existing = next((L for L in loops if L["header"] == hdr), None)
            if existing is not None:
                existing["nodes"] |= nodes
            else:
                loops.append({"header": hdr, "nodes": nodes})
        # nesting depth per loop (memoised recursion)
        depths: dict[int, int] = {}

        def depth(idx: int) -> int:
            if idx in depths:
                return depths[idx]
            outer = [
                k for k, L in enumerate(loops)
                if k != idx and loops[idx]["header"] in L["nodes"]
            ]
            d = 1 + (max(depth(k) for k in outer) if outer else 0)
            depths[idx] = d
            return d

        for k in range(len(loops)):
            depth(k)
        for k, L in enumerate(loops):
            L["depth"] = depths[k]
        return loops


def _opcode_bucket(op: str) -> str:
    if op in INT_ARITH:
        return "int"
    if op in FP_ARITH:
        return "fp"
    if op in BITWISE:
        return "bit"
    if op in CASTS:
        return "cast"
    if op in MEMORY:
        return "mem"
    if op in TERMINATORS:
        return "term"
    return "other"


# Weighted static cost model (proxy for execution work of the compiled
# program).  Weights are relative latency/energy class costs; the model is
# used by the Stage-2 cost-benefit gate as B-hat input, and by the GA as a
# fast fitness proxy.  Documented as an estimate, not a measurement.
STATIC_COST = {
    "load": 4.0, "store": 4.0, "alloca": 1.0, "getelementptr": 2.0,
    "call": 12.0, "br": 2.0, "switch": 3.0, "ret": 1.0,
    "icmp": 1.0, "fcmp": 2.0, "phi": 1.0, "select": 1.0,
    "add": 1.0, "sub": 1.0, "mul": 3.0, "udiv": 15.0, "sdiv": 15.0,
    "urem": 15.0, "srem": 15.0,
    "fadd": 4.0, "fsub": 4.0, "fmul": 4.0, "fdiv": 8.0, "frem": 8.0,
    "shl": 1.0, "lshr": 1.0, "ashr": 1.0, "and": 1.0, "or": 1.0, "xor": 1.0,
}


def static_cost(ir_or_text) -> float:
    """Weighted static cost of a module (lower ~= cheaper to execute)."""
    mod = _as_module_ref(ir_or_text)
    total = 0.0
    for fn in mod.functions:
        if fn.is_declaration:
            continue
        for blk in fn.blocks:
            for ins in blk.instructions:
                total += STATIC_COST.get(ins.opcode, 1.0)
    return total


def _as_module_ref(x) -> binding.ModuleRef:
    if isinstance(x, binding.ModuleRef):
        return x
    if isinstance(x, ir.Module):
        return binding.parse_assembly(str(x))
    if isinstance(x, str):
        return binding.parse_assembly(x)
    raise TypeError(f"cannot parse {type(x)!r} as LLVM IR")


def count_instructions(ir_or_text) -> int:
    """Cheap instruction count used by experiment reports."""
    mod = _as_module_ref(ir_or_text)
    n = 0
    for fn in mod.functions:
        if fn.is_declaration:
            continue
        for blk in fn.blocks:
            n += sum(1 for _ in blk.instructions)
    return n


class IRFeatureExtractor:
    """Extract 40+ typed metrics from LLVM IR by walking binding values."""

    def __init__(self, module):
        self.module = _as_module_ref(module)
        self.ir_text = str(self.module)

    # -------------------------------------------------------------------------
    def extract_features(self) -> dict:
        mod = self.module
        f = {
            "num_functions": 0,
            "num_function_declarations": 0,
            "num_global_variables": 0,
            "num_arguments": 0,
            "num_basic_blocks": 0,
            "total_instructions": 0,
            "num_loads": 0,
            "num_stores": 0,
            "num_alloca": 0,
            "num_gep": 0,
            "num_calls": 0,
            "num_ret": 0,
            "num_branches": 0,
            "num_cond_branches": 0,
            "num_uncond_branches": 0,
            "num_switch": 0,
            "num_icmp": 0,
            "num_fcmp": 0,
            "num_phi": 0,
            "num_select": 0,
            "num_int_arith": 0,
            "num_fp_arith": 0,
            "num_bitwise": 0,
            "num_casts": 0,
            "num_const_operands": 0,
            "num_const_int_operands": 0,
            "num_const_float_operands": 0,
            "num_terminators": 0,
            "num_entry_blocks": 0,
            "num_exit_blocks": 0,
            "num_back_edges": 0,
            "num_natural_loops": 0,
            "max_loop_depth": 0,
            "num_critical_edges": 0,
            "max_block_instructions": 0,
            "avg_block_instructions": 0.0,
            "max_function_instructions": 0,
            "avg_instructions_per_function": 0.0,
            "avg_predecessors": 0.0,
            "max_successors": 0,
            "cyclomatic_complexity": 0,
            "mem_ops": 0,
            "mem_ratio": 0.0,
            "fp_ratio": 0.0,
            "branch_ratio": 0.0,
            "estimated_ir_bytes": len(self.ir_text),
            "static_cost": 0.0,
            "uses_float": 0,
        }

        block_lengths: list[int] = []
        fn_instr_counts: list[int] = []
        total_preds = 0
        total_blocks_with_preds = 0

        for fn in mod.functions:
            if fn.is_declaration:
                f["num_function_declarations"] += 1
                continue
            f["num_functions"] += 1
            f["num_arguments"] += sum(1 for _ in fn.arguments)
            blocks = list(fn.blocks)
            f["num_basic_blocks"] += len(blocks)

            cfg = _CFG(blocks)
            dom = cfg.dominators() if blocks else []
            if blocks:
                f["num_entry_blocks"] += 1  # entry is blocks[0]
                loops = cfg.natural_loops(dom) if dom else []
                f["num_natural_loops"] += len(loops)
                f["num_back_edges"] += len(cfg.back_edges(dom)) if dom else 0
                if loops:
                    f["max_loop_depth"] = max(
                        f["max_loop_depth"], max(L["depth"] for L in loops))
                # critical edges
                for i, succs in enumerate(cfg.succ):
                    if len(succs) > 1:
                        for j in succs:
                            if len(cfg.pred[j]) > 1:
                                f["num_critical_edges"] += 1
                for preds in cfg.pred:
                    if preds:
                        total_preds += len(preds)
                        total_blocks_with_preds += 1
                f["max_successors"] = max(
                    f["max_successors"], max((len(s) for s in cfg.succ), default=0))

            fn_count = 0
            for blk in blocks:
                n_ins = 0
                last_ins = None
                for ins in blk.instructions:
                    n_ins += 1
                    last_ins = ins
                    self._count_instruction(ins, f)
                block_lengths.append(n_ins)
                fn_count += n_ins
                if last_ins is not None and last_ins.opcode == "ret":
                    f["num_exit_blocks"] += 1
            fn_instr_counts.append(fn_count)
            f["max_function_instructions"] = max(
                f["max_function_instructions"], fn_count)

        # globals (module-level variables with an initializer)
        try:
            f["num_global_variables"] = sum(
                1 for g in mod.global_variables if not g.is_declaration)
        except Exception:
            f["num_global_variables"] = 0

        # ---- derived ----
        nb = f["num_basic_blocks"]
        f["avg_block_instructions"] = (
            sum(block_lengths) / len(block_lengths) if block_lengths else 0.0)
        f["max_block_instructions"] = max(block_lengths, default=0)
        f["total_instructions"] = sum(block_lengths)
        if fn_instr_counts:
            f["avg_instructions_per_function"] = (
                sum(fn_instr_counts) / len(fn_instr_counts))
        f["avg_predecessors"] = (
            total_preds / total_blocks_with_preds if total_blocks_with_preds else 0.0)

        # cyclomatic complexity: E - N + 2P over the whole module
        edges = 0
        nodes = 0
        for fn in mod.functions:
            if fn.is_declaration:
                continue
            cfg = _CFG(list(fn.blocks))
            edges += sum(len(s) for s in cfg.succ)
            nodes += len(cfg.blocks)
        P = f["num_functions"]
        f["cyclomatic_complexity"] = max(1, edges - nodes + 2 * P)

        ti = f["total_instructions"] or 1
        f["mem_ops"] = (f["num_loads"] + f["num_stores"]
                        + f["num_alloca"] + f["num_gep"])
        f["mem_ratio"] = f["mem_ops"] / ti
        fp = (f["num_fp_arith"] + f["num_fcmp"])
        f["fp_ratio"] = fp / ti
        f["uses_float"] = 1 if fp > 0 else 0
        f["branch_ratio"] = (f["num_cond_branches"] + f["num_switch"]) / ti
        f["static_cost"] = static_cost(mod)

        # ---- legacy aliases (original 13 string-based metrics) ----
        f["num_alu_ops"] = f["num_int_arith"] + f["num_bitwise"]
        f["num_fp_ops"] = f["num_fp_arith"]
        f["num_allocas"] = f["num_alloca"]
        f["cyclomatic_complexity_estimate"] = f["cyclomatic_complexity"]
        return f

    # -------------------------------------------------------------------------
    def _count_instruction(self, ins, f: dict) -> None:
        op = ins.opcode
        if op in MEMORY:
            if op == "load":
                f["num_loads"] += 1
            elif op == "store":
                f["num_stores"] += 1
            elif op == "alloca":
                f["num_alloca"] += 1
            elif op == "getelementptr":
                f["num_gep"] += 1
        elif op == "call":
            f["num_calls"] += 1
        elif op == "ret":
            f["num_ret"] += 1
            f["num_terminators"] += 1
        elif op == "br":
            f["num_branches"] += 1
            f["num_terminators"] += 1
            labels = {
                o.name for o in ins.operands
                if o.value_kind == binding.ValueKind.basic_block
            }
            if len(labels) >= 2:
                f["num_cond_branches"] += 1
            else:
                f["num_uncond_branches"] += 1
        elif op == "switch":
            f["num_switch"] += 1
            f["num_branches"] += 1
            f["num_terminators"] += 1
        elif op in TERMINATORS:
            f["num_terminators"] += 1
        elif op == "icmp":
            f["num_icmp"] += 1
        elif op == "fcmp":
            f["num_fcmp"] += 1
        elif op == "phi":
            f["num_phi"] += 1
        elif op == "select":
            f["num_select"] += 1
        elif op in FP_ARITH:
            f["num_fp_arith"] += 1
        elif op in INT_ARITH:
            f["num_int_arith"] += 1
        elif op in BITWISE:
            f["num_bitwise"] += 1
        elif op in CASTS:
            f["num_casts"] += 1

        # operand constants
        for o in ins.operands:
            if not o.is_constant:
                continue
            f["num_const_operands"] += 1
            if o.value_kind == binding.ValueKind.constant_int:
                f["num_const_int_operands"] += 1
            elif o.value_kind == binding.ValueKind.constant_fp:
                f["num_const_float_operands"] += 1


if __name__ == "__main__":
    print("stage2.extractor: IRFeatureExtractor ready")
