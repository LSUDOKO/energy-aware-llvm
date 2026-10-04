from llvmlite import ir, binding
from frontend.ast_nodes import *

CMP_PREDICATES = {
    'EQ': '==', 'NEQ': '!=', 'LT': '<', 'GT': '>', 'LE': '<=', 'GE': '>=',
}

class CodeGenError(Exception):
    pass

class CodeGenVisitor:
    """Conventional (baseline) emitter: one walk, checks inline, no folding."""

    def __init__(self):
        # Initialize LLVM
        binding.initialize_native_target()
        binding.initialize_native_asmprinter()

        # Setup Module, Target, and Builder
        self.module = ir.Module(name="minic_module")
        self.module.triple = binding.get_default_triple()
        target = binding.Target.from_default_triple()
        target_machine = target.create_target_machine()
        self.module.data_layout = target_machine.target_data

        self.builder = None

        # Symbol table for variables in the current scope
        # Maps var name to its LLVM alloca instruction
        self.symtab = {}

        # Function signatures declared in this module (name -> ir.Function)
        self.functions = {}

        # Predefined types
        self.i32 = ir.IntType(32)
        self.f32 = ir.FloatType()

    def generate_code(self, node):
        return self.visit(node)

    def visit(self, node):
        method_name = f'visit_{type(node).__name__}'
        visitor = getattr(self, method_name, self.generic_visit)
        return visitor(node)

    def generic_visit(self, node):
        raise CodeGenError(f'No visit_{type(node).__name__} method')

    def visit_Program(self, node):
        # Pre-declare all function signatures so forward references work.
        for func in node.functions:
            self._declare_function(func)
        for func in node.functions:
            self.visit(func)
        return self.module

    def _declare_function(self, node):
        ret_type = self.i32 if node.return_type == 'int' else self.f32
        param_types = [self.i32 if t == 'int' else self.f32 for t, _ in node.params]
        fnty = ir.FunctionType(ret_type, param_types)
        func = ir.Function(self.module, fnty, name=node.name)
        self.functions[node.name] = func
        return func

    def visit_FunctionDecl(self, node):
        func = self.functions.get(node.name)
        if func is None:
            func = self._declare_function(node)

        # Create entry block
        block = func.append_basic_block(name="entry")
        self.builder = ir.IRBuilder(block)

        # Fresh symbol table for the new function
        self.symtab = {}

        # Bind parameters: store each incoming argument to an alloca
        for (ptype, pname), arg in zip(node.params, func.args):
            arg.name = pname
            alloca = self.builder.alloca(self.i32 if ptype == 'int' else self.f32, name=pname)
            self.builder.store(arg, alloca)
            self.symtab[pname] = alloca

        self.visit(node.body)

        # If no explicit return, add a default one
        if not self.builder.block.is_terminated:
            self.builder.ret(ir.Constant(func.function_type.return_type, 0))

        return func

    def visit_Block(self, node):
        for stmt in node.statements:
            if self.builder.block.is_terminated:
                break  # unreachable-code elimination
            self.visit(stmt)

    def visit_VarDecl(self, node):
        var_type = self.i32 if node.var_type == 'int' else self.f32
        # Allocate space on the stack for the variable
        alloca = self.builder.alloca(var_type, name=node.name)
        self.symtab[node.name] = alloca

        if node.init_expr:
            val = self._coerce(self.visit(node.init_expr), var_type)
            self.builder.store(val, alloca)

    def visit_AssignStmt(self, node):
        if node.name not in self.symtab:
            raise CodeGenError(f"Undefined variable '{node.name}'")
        ptr = self.symtab[node.name]
        val = self._coerce(self.visit(node.expr), ptr.type.pointee)
        self.builder.store(val, ptr)

    def visit_ReturnStmt(self, node):
        ret_type = self.builder.function.function_type.return_type
        val = self._coerce(self.visit(node.expr), ret_type)
        self.builder.ret(val)

    def visit_CallExpr(self, node):
        if node.name not in self.functions:
            raise CodeGenError(f"Call to undeclared function '{node.name}'")
        func = self.functions[node.name]
        ftype = func.function_type
        if len(node.args) != len(ftype.args):
            raise CodeGenError(
                f"Function '{node.name}' expects {len(ftype.args)} argument(s), got {len(node.args)}")
        args = [self._coerce(self.visit(a), t)
                for a, t in zip(node.args, ftype.args)]
        return self.builder.call(func, args, 'calltmp')

    def visit_IfStmt(self, node):
        cond_val = self.visit(node.condition)
        # Assuming cond_val is an integer, compare it to 0
        cond_bool = self._condition_to_bool(cond_val)

        if node.else_branch:
            then_bb = self.builder.append_basic_block('then')
            else_bb = self.builder.append_basic_block('else')
            merge_bb = self.builder.append_basic_block('ifcont')

            self.builder.cbranch(cond_bool, then_bb, else_bb)

            # Emit Then
            self.builder.position_at_end(then_bb)
            self.visit(node.then_branch)
            if not self.builder.block.is_terminated:
                self.builder.branch(merge_bb)

            # Emit Else
            self.builder.position_at_end(else_bb)
            self.visit(node.else_branch)
            if not self.builder.block.is_terminated:
                self.builder.branch(merge_bb)

            self.builder.position_at_end(merge_bb)
        else:
            then_bb = self.builder.append_basic_block('then')
            merge_bb = self.builder.append_basic_block('ifcont')

            self.builder.cbranch(cond_bool, then_bb, merge_bb)

            # Emit Then
            self.builder.position_at_end(then_bb)
            self.visit(node.then_branch)
            if not self.builder.block.is_terminated:
                self.builder.branch(merge_bb)

            self.builder.position_at_end(merge_bb)

    def visit_WhileStmt(self, node):
        cond_bb = self.builder.append_basic_block('whilecond')
        loop_bb = self.builder.append_basic_block('whileloop')
        after_bb = self.builder.append_basic_block('whileafter')

        self.builder.branch(cond_bb)
        self.builder.position_at_end(cond_bb)

        cond_val = self.visit(node.condition)
        cond_bool = self._condition_to_bool(cond_val)
        self.builder.cbranch(cond_bool, loop_bb, after_bb)

        self.builder.position_at_end(loop_bb)
        self.visit(node.body)
        if not self.builder.block.is_terminated:
            self.builder.branch(cond_bb)

        self.builder.position_at_end(after_bb)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _coerce(self, value, target):
        """Implicit int<->float conversion to ``target`` (C semantics:
        int->float is sitofp, float->int truncates toward zero)."""
        if value.type == target:
            return value
        if isinstance(target, ir.FloatType):
            if isinstance(value.type, ir.IntType):
                if value.type.width == 1:
                    value = self.builder.zext(value, self.i32, 'zexttmp')
                return self.builder.sitofp(value, target, 'sitofptmp')
        elif isinstance(target, ir.IntType) and isinstance(value.type, ir.FloatType):
            return self.builder.fptosi(value, target, 'trunctmp')
        elif isinstance(target, ir.IntType) and isinstance(value.type, ir.IntType):
            if value.type.width == 1:
                return self.builder.zext(value, target, 'zexttmp')
        raise CodeGenError(f"cannot convert {value.type} to {target}")

    def _condition_to_bool(self, cond_val):
        """Numeric -> i1 truthiness (non-zero is true)."""
        if isinstance(cond_val.type, ir.FloatType):
            return self.builder.fcmp_unordered(
                '!=', cond_val, ir.Constant(self.f32, 0.0), 'ifcond')
        if isinstance(cond_val.type, ir.IntType) and cond_val.type.width == 1:
            return cond_val
        return self.builder.icmp_signed('!=', cond_val, ir.Constant(self.i32, 0), 'ifcond')

    def visit_BinaryOp(self, node):
        left = self.visit(node.left)
        right = self.visit(node.right)
        if isinstance(left.type, ir.FloatType) != isinstance(right.type, ir.FloatType):
            left = self._coerce(left, self.f32)    # usual arithmetic conversions
            right = self._coerce(right, self.f32)

        if node.op in CMP_PREDICATES:
            pred = CMP_PREDICATES[node.op]
            if isinstance(left.type, ir.FloatType) or isinstance(right.type, ir.FloatType):
                # C: every relation with NaN is false except '!=' (true)
                emit = (self.builder.fcmp_unordered if pred == '!='
                        else self.builder.fcmp_ordered)
                cmp = emit(pred, left, right, 'cmptmp')
            else:
                cmp = self.builder.icmp_signed(pred, left, right, 'cmptmp')
            return self.builder.zext(cmp, self.i32, 'zexttmp')

        is_float = isinstance(left.type, ir.FloatType)
        if node.op == 'PLUS':
            return self.builder.fadd(left, right, 'faddtmp') if is_float else self.builder.add(left, right, 'addtmp')
        elif node.op == 'MINUS':
            return self.builder.fsub(left, right, 'fsubtmp') if is_float else self.builder.sub(left, right, 'subtmp')
        elif node.op == 'MUL':
            return self.builder.fmul(left, right, 'fmultmp') if is_float else self.builder.mul(left, right, 'multmp')
        elif node.op == 'DIV':
            return self.builder.fdiv(left, right, 'fdivtmp') if is_float else self.builder.sdiv(left, right, 'divtmp')
        else:
            raise CodeGenError(f"Unknown operator {node.op}")

    def visit_UnaryOp(self, node):
        val = self.visit(node.operand)
        if node.op == 'MINUS':
            if isinstance(val.type, ir.FloatType):
                return self.builder.fsub(ir.Constant(self.f32, 0.0), val, 'negtmp')
            return self.builder.neg(val, 'negtmp')
        elif node.op == 'NOT':
            return self.builder.zext(self._condition_to_bool_neg(val), self.i32, 'zexttmp')
        raise CodeGenError(f"Unknown unary operator {node.op}")

    def _condition_to_bool_neg(self, val):
        """!x -> (x == 0)"""
        if isinstance(val.type, ir.FloatType):
            return self.builder.fcmp_ordered('==', val, ir.Constant(self.f32, 0.0), 'nottmp')
        return self.builder.icmp_signed('==', val, ir.Constant(self.i32, 0), 'nottmp')

    def visit_Number(self, node):
        if node.is_float:
            return ir.Constant(self.f32, node.value)
        return ir.Constant(self.i32, node.value)

    def visit_Identifier(self, node):
        if node.name not in self.symtab:
            raise CodeGenError(f"Undefined variable '{node.name}'")
        ptr = self.symtab[node.name]
        return self.builder.load(ptr, node.name)
