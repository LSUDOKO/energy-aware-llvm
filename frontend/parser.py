from frontend.lexer import Lexer, Token
from frontend.ast_nodes import *

COMPARISON_OPS = ('LT', 'GT', 'LE', 'GE')
EQUALITY_OPS = ('EQ', 'NEQ')
ADDITIVE_OPS = ('PLUS', 'MINUS')
MULTIPLICATIVE_OPS = ('MUL', 'DIV', 'MOD')
UNARY_OPS = ('MINUS', 'NOT')
TYPE_KEYWORDS = ('INT_KW', 'FLOAT_KW')

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
        start = self.current()
        ret_type = self.consume().value  # 'int' or 'float'
        name = self.consume('IDENTIFIER').value
        self.consume('LPAREN')
        params = []
        if self.current().type != 'RPAREN':
            while True:
                ptype_tok = self.consume()
                if ptype_tok.type not in TYPE_KEYWORDS:
                    raise ParseError(f"Expected parameter type, got {ptype_tok.type} at line {ptype_tok.line}")
                pname = self.consume('IDENTIFIER').value
                params.append((ptype_tok.value, pname))
                if not self.match('COMMA'):
                    break
        self.consume('RPAREN')
        body = self.parse_block()
        return FunctionDecl(ret_type, name, params, body, loc=(start.line, start.column))

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
        elif tok.type == 'FOR_KW':
            return self.parse_for()
        elif tok.type == 'IDENTIFIER':
            # Assignment or (future) expression statement; assignment for now
            return self.parse_assignment()
        else:
            raise ParseError(f"Unexpected token {tok} in statement")

    def parse_var_decl(self):
        tok = self.consume()
        var_type = tok.value
        name = self.consume('IDENTIFIER').value
        init_expr = None
        if self.match('ASSIGN'):
            init_expr = self.parse_expression()
        self.consume('SEMI')
        return VarDecl(var_type, name, init_expr, loc=(tok.line, tok.column))

    def parse_return(self):
        tok = self.consume('RETURN_KW')
        expr = self.parse_expression()
        self.consume('SEMI')
        return ReturnStmt(expr, loc=(tok.line, tok.column))

    def parse_if(self):
        tok = self.consume('IF_KW')
        self.consume('LPAREN')
        cond = self.parse_expression()
        self.consume('RPAREN')
        then_branch = self.parse_block()
        else_branch = None
        if self.match('ELSE_KW'):
            else_branch = self.parse_block()
        return IfStmt(cond, then_branch, else_branch, loc=(tok.line, tok.column))

    def parse_while(self):
        tok = self.consume('WHILE_KW')
        self.consume('LPAREN')
        cond = self.parse_expression()
        self.consume('RPAREN')
        body = self.parse_block()
        return WhileStmt(cond, body, loc=(tok.line, tok.column))

    COMPOUND_OPS = {'PLUSEQ': 'PLUS', 'MINUSEQ': 'MINUS',
                    'MULEQ': 'MUL', 'DIVEQ': 'DIV'}

    def parse_simple_assignment(self):
        """``x = e``, ``x += e`` (also -= *= /=), ``x++``, ``x--`` (no ';').
        Compound forms are desugared to a plain assignment."""
        tok = self.consume('IDENTIFIER')
        name, loc = tok.value, (tok.line, tok.column)
        op = self.consume()
        if op.type == 'ASSIGN':
            return AssignStmt(name, self.parse_expression(), loc=loc)
        if op.type in self.COMPOUND_OPS:
            rhs = self.parse_expression()
            return AssignStmt(name, BinaryOp(self.COMPOUND_OPS[op.type],
                                             Identifier(name, loc=loc), rhs, loc=loc), loc=loc)
        if op.type in ('INC', 'DEC'):
            one = Number(1, False, loc=loc)
            return AssignStmt(name, BinaryOp('PLUS' if op.type == 'INC' else 'MINUS',
                                             Identifier(name, loc=loc), one, loc=loc), loc=loc)
        raise ParseError(f"Expected assignment operator, got {op.type} at line {op.line}")

    def parse_assignment(self):
        stmt = self.parse_simple_assignment()
        self.consume('SEMI')
        return stmt

    def parse_for(self):
        """``for (init; cond; step) { body }`` desugars to
        ``{ init; while (cond) { body; step; } }`` (init is scoped to the loop)."""
        tok = self.consume('FOR_KW')
        self.consume('LPAREN')
        if self.current().type in TYPE_KEYWORDS:
            init = self.parse_var_decl()          # consumes its ';'
        else:
            init = self.parse_assignment()
        cond = self.parse_expression()
        self.consume('SEMI')
        step = self.parse_simple_assignment()
        self.consume('RPAREN')
        body = self.parse_block()
        body.statements.append(step)
        loop = WhileStmt(cond, body, loc=(tok.line, tok.column))
        return Block([init, loop])

    # ------------------------------------------------------------------
    # Expressions (precedence: equality > relational > additive >
    # multiplicative > unary > primary)
    # ------------------------------------------------------------------

    def parse_expression(self):
        return self.parse_equality()

    def parse_equality(self):
        node = self.parse_relational()
        while tok := self._match_any(EQUALITY_OPS):
            right = self.parse_relational()
            node = BinaryOp(tok.type, node, right, loc=(tok.line, tok.column))
        return node

    def parse_relational(self):
        node = self.parse_additive()
        while tok := self._match_any(COMPARISON_OPS):
            right = self.parse_additive()
            node = BinaryOp(tok.type, node, right, loc=(tok.line, tok.column))
        return node

    def parse_additive(self):
        node = self.parse_multiplicative()
        while tok := self._match_any(ADDITIVE_OPS):
            right = self.parse_multiplicative()
            node = BinaryOp(tok.type, node, right, loc=(tok.line, tok.column))
        return node

    def parse_multiplicative(self):
        node = self.parse_unary()
        while tok := self._match_any(MULTIPLICATIVE_OPS):
            right = self.parse_unary()
            node = BinaryOp(tok.type, node, right, loc=(tok.line, tok.column))
        return node

    def parse_unary(self):
        tok = self.current()
        if tok.type in UNARY_OPS:
            self.consume()
            operand = self.parse_unary()
            return UnaryOp(tok.type, operand, loc=(tok.line, tok.column))
        return self.parse_primary()

    def parse_primary(self):
        tok = self.consume()
        if tok.type == 'NUMBER':
            if '.' in tok.value:
                return Number(float(tok.value), True, loc=(tok.line, tok.column))
            return Number(int(tok.value), False, loc=(tok.line, tok.column))
        elif tok.type == 'IDENTIFIER':
            if self.current().type == 'LPAREN':
                self.consume('LPAREN')
                args = []
                if self.current().type != 'RPAREN':
                    while True:
                        args.append(self.parse_expression())
                        if not self.match('COMMA'):
                            break
                self.consume('RPAREN')
                return CallExpr(tok.value, args, loc=(tok.line, tok.column))
            return Identifier(tok.value, loc=(tok.line, tok.column))
        elif tok.type == 'LPAREN':
            expr = self.parse_expression()
            self.consume('RPAREN')
            return expr
        else:
            raise ParseError(f"Unexpected token {tok} in expression")

    def _match_any(self, types):
        for t in types:
            tok = self.match(t)
            if tok is not None:
                return tok
        return None
