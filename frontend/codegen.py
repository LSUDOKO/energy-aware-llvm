from llvmlite import ir, binding
from frontend.ast_nodes import *

class CodeGenError(Exception):
    pass

class CodeGenVisitor:
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
        for func in node.functions:
            self.visit(func)
        return self.module

    def visit_FunctionDecl(self, node):
        # Determine return type
        ret_type = self.i32 if node.return_type == 'int' else self.f32
        
        # Create function type (no args for now)
        fnty = ir.FunctionType(ret_type, [])
        func = ir.Function(self.module, fnty, name=node.name)
        
        # Create entry block
        block = func.append_basic_block(name="entry")
        self.builder = ir.IRBuilder(block)
        
        # Clear symbol table for new function
        self.symtab = {}
        
        self.visit(node.body)
        
        # If no explicit return, add a default one (only for void/main usually, but let's be safe)
        if not self.builder.block.is_terminated:
            self.builder.ret(ir.Constant(ret_type, 0))
            
        return func

    def visit_Block(self, node):
        for stmt in node.statements:
            self.visit(stmt)

    def visit_VarDecl(self, node):
        var_type = self.i32 if node.var_type == 'int' else self.f32
        # Allocate space on the stack for the variable
        alloca = self.builder.alloca(var_type, name=node.name)
        self.symtab[node.name] = alloca
        
        if node.init_expr:
            val = self.visit(node.init_expr)
            self.builder.store(val, alloca)

    def visit_AssignStmt(self, node):
        if node.name not in self.symtab:
            raise CodeGenError(f"Undefined variable '{node.name}'")
        val = self.visit(node.expr)
        ptr = self.symtab[node.name]
        self.builder.store(val, ptr)

    def visit_ReturnStmt(self, node):
        val = self.visit(node.expr)
        self.builder.ret(val)

    def visit_IfStmt(self, node):
        cond_val = self.visit(node.condition)
        # Assuming cond_val is an integer, compare it to 0
        zero = ir.Constant(self.i32, 0)
        cond_bool = self.builder.icmp_signed('!=', cond_val, zero, 'ifcond')
        
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
        zero = ir.Constant(self.i32, 0)
        cond_bool = self.builder.icmp_signed('!=', cond_val, zero, 'whilecond_check')
        self.builder.cbranch(cond_bool, loop_bb, after_bb)
        
        self.builder.position_at_end(loop_bb)
        self.visit(node.body)
        if not self.builder.block.is_terminated:
            self.builder.branch(cond_bb)
            
        self.builder.position_at_end(after_bb)

    def visit_BinaryOp(self, node):
        left = self.visit(node.left)
        right = self.visit(node.right)
        
        if node.op == 'PLUS':
            return self.builder.add(left, right, 'addtmp')
        elif node.op == 'MINUS':
            return self.builder.sub(left, right, 'subtmp')
        elif node.op == 'MUL':
            return self.builder.mul(left, right, 'multmp')
        elif node.op == 'DIV':
            return self.builder.sdiv(left, right, 'divtmp')
        elif node.op == 'EQ':
            cmp = self.builder.icmp_signed('==', left, right, 'eqtmp')
            return self.builder.zext(cmp, self.i32, 'zexttmp')
        elif node.op == 'LT':
            cmp = self.builder.icmp_signed('<', left, right, 'lttmp')
            return self.builder.zext(cmp, self.i32, 'zexttmp')
        elif node.op == 'GT':
            cmp = self.builder.icmp_signed('>', left, right, 'gttmp')
            return self.builder.zext(cmp, self.i32, 'zexttmp')
        else:
            raise CodeGenError(f"Unknown operator {node.op}")

    def visit_Number(self, node):
        if node.is_float:
            return ir.Constant(self.f32, node.value)
        return ir.Constant(self.i32, node.value)

    def visit_Identifier(self, node):
        if node.name not in self.symtab:
            raise CodeGenError(f"Undefined variable '{node.name}'")
        ptr = self.symtab[node.name]
        return self.builder.load(ptr, node.name)
