from frontend.lexer import Lexer, Token
from frontend.ast_nodes import *

class ParseError(Exception):
    pass

class Parser:
    def __init__(self, tokens):
        self.tokens = tokens
        self.pos = 0

    def current(self) -> Token:
        return self.tokens[self.pos]

    def consume(self, expected_type=None):
        tok = self.current()
        if expected_type and tok.type != expected_type:
            raise ParseError(f"Expected {expected_type}, got {tok.type} at line {tok.line}")
        self.pos += 1
        return tok

    def match(self, expected_type):
        if self.current().type == expected_type:
            return self.consume()
        return None

    def parse(self):
        functions = []
        while self.current().type != 'EOF':
            functions.append(self.parse_function())
        return Program(functions)

    def parse_function(self):
        # int main() { ... }
        ret_type = self.consume().value # 'int' or 'float'
        name = self.consume('IDENTIFIER').value
        self.consume('LPAREN')
        # TODO: Parse parameters
        self.consume('RPAREN')
        body = self.parse_block()
        return FunctionDecl(ret_type, name, [], body)

    def parse_block(self):
        self.consume('LBRACE')
        statements = []
        while self.current().type != 'RBRACE' and self.current().type != 'EOF':
            statements.append(self.parse_statement())
        self.consume('RBRACE')
        return Block(statements)

    def parse_statement(self):
        tok = self.current()
        if tok.type in ('INT_KW', 'FLOAT_KW'):
            return self.parse_var_decl()
        elif tok.type == 'RETURN_KW':
            return self.parse_return()
        elif tok.type == 'IF_KW':
            return self.parse_if()
        elif tok.type == 'WHILE_KW':
            return self.parse_while()
        elif tok.type == 'IDENTIFIER':
            # Could be assignment or function call. Let's do assignment for now
            return self.parse_assignment()
        else:
            raise ParseError(f"Unexpected token {tok} in statement")

    def parse_var_decl(self):
        var_type = self.consume().value
        name = self.consume('IDENTIFIER').value
        init_expr = None
        if self.match('ASSIGN'):
            init_expr = self.parse_expression()
        self.consume('SEMI')
        return VarDecl(var_type, name, init_expr)

    def parse_return(self):
        self.consume('RETURN_KW')
        expr = self.parse_expression()
        self.consume('SEMI')
        return ReturnStmt(expr)

    def parse_if(self):
        self.consume('IF_KW')
        self.consume('LPAREN')
        cond = self.parse_expression()
        self.consume('RPAREN')
        then_branch = self.parse_block()
        else_branch = None
        if self.match('ELSE_KW'):
            else_branch = self.parse_block()
        return IfStmt(cond, then_branch, else_branch)

    def parse_while(self):
        self.consume('WHILE_KW')
        self.consume('LPAREN')
        cond = self.parse_expression()
        self.consume('RPAREN')
        body = self.parse_block()
        return WhileStmt(cond, body)

    def parse_assignment(self):
        name = self.consume('IDENTIFIER').value
        self.consume('ASSIGN')
        expr = self.parse_expression()
        self.consume('SEMI')
        return AssignStmt(name, expr)

    def parse_expression(self):
        return self.parse_equality()

    def parse_equality(self):
        node = self.parse_additive()
        while tok := (self.match('EQ') or self.match('LT') or self.match('GT')):
            right = self.parse_additive()
            node = BinaryOp(tok.type, node, right)
        return node

    def parse_additive(self):
        node = self.parse_multiplicative()
        while tok := (self.match('PLUS') or self.match('MINUS')):
            right = self.parse_multiplicative()
            node = BinaryOp(tok.type, node, right)
        return node

    def parse_multiplicative(self):
        node = self.parse_primary()
        while tok := (self.match('MUL') or self.match('DIV')):
            right = self.parse_primary()
            node = BinaryOp(tok.type, node, right)
        return node

    def parse_primary(self):
        tok = self.consume()
        if tok.type == 'NUMBER':
            if '.' in tok.value:
                return Number(float(tok.value), True)
            return Number(int(tok.value), False)
        elif tok.type == 'IDENTIFIER':
            return Identifier(tok.value)
        elif tok.type == 'LPAREN':
            expr = self.parse_expression()
            self.consume('RPAREN')
            return expr
        else:
            raise ParseError(f"Unexpected token {tok} in expression")
