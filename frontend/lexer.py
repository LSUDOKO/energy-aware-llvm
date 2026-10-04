import re
from typing import NamedTuple, List, Optional

class Token(NamedTuple):
    type: str
    value: str
    line: int
    column: int

class LexerError(Exception):
    pass

class Lexer:
    # Token specs; order matters (longer operators before their prefixes:
    # ==/=, !=/!-, <=/<, >=/>).
    TOKEN_SPECS = [
        ('NUMBER',     r'\d+(\.\d*)?'),  # Integer or decimal number
        ('INT_KW',     r'\bint\b'),      # int keyword
        ('FLOAT_KW',   r'\bfloat\b'),    # float keyword
        ('RETURN_KW',  r'\breturn\b'),   # return keyword
        ('IF_KW',      r'\bif\b'),       # if keyword
        ('ELSE_KW',    r'\belse\b'),     # else keyword
        ('WHILE_KW',   r'\bwhile\b'),    # while keyword
        ('EQ',         r'=='),           # Equal
        ('NEQ',        r'!='),           # Not equal
        ('LE',         r'<='),           # Less or equal
        ('GE',         r'>='),           # Greater or equal
        ('LT',         r'<'),            # Less than
        ('GT',         r'>'),            # Greater than
        ('ASSIGN',     r'='),            # Assignment operator
        ('PLUS',       r'\+'),           # Addition
        ('MINUS',      r'-'),            # Subtraction / unary minus
        ('MUL',        r'\*'),           # Multiplication
        ('DIV',        r'/'),            # Division
        ('NOT',        r'!'),            # Logical not
        ('IDENTIFIER', r'[A-Za-z_][A-Za-z0-9_]*'), # Identifiers
        ('LPAREN',     r'\('),           # Left parenthesis
        ('RPAREN',     r'\)'),           # Right parenthesis
        ('LBRACE',     r'\{'),           # Left brace
        ('RBRACE',     r'\}'),           # Right brace
        ('SEMI',       r';'),            # Semicolon
        ('COMMA',      r','),            # Comma
        ('WS',         r'[ \t]+'),       # Whitespace
        ('NEWLINE',    r'\n'),           # Line endings
        ('COMMENT',    r'//.*'),         # Comments
        ('MISMATCH',   r'.'),            # Any other character
    ]

    # Compile regex
    TOK_REGEX = '|'.join(f'(?P<{pair[0]}>{pair[1]})' for pair in TOKEN_SPECS)
    GET_TOKEN = re.compile(TOK_REGEX).match

    def __init__(self, code: str):
        self.original_code = code
        # Strip /* */ block comments while preserving newlines so that
        # line/column tracking stays correct.
        self.code = self._strip_block_comments(code)
        self.tokens: List[Token] = []
        self.tokenize()

    @staticmethod
    def _strip_block_comments(code: str) -> str:
        def repl(match: "re.Match") -> str:
            return '\n' * match.group(0).count('\n')
        stripped = re.sub(r'/\*.*?\*/', repl, code, flags=re.DOTALL)
        if '/*' in stripped:
            raise LexerError('unterminated block comment')
        return stripped

    def tokenize(self):
        line_num = 1
        line_start = 0
        mo = self.GET_TOKEN(self.code)
        while mo is not None:
            kind = mo.lastgroup
            value = mo.group(kind)
            if kind == 'NEWLINE':
                line_start = mo.end()
                line_num += 1
            elif kind == 'WS' or kind == 'COMMENT':
                pass
            elif kind == 'MISMATCH':
                raise LexerError(f'{value!r} unexpected on line {line_num}')
            else:
                column = mo.start() - line_start
                self.tokens.append(Token(kind, value, line_num, column))
            mo = self.GET_TOKEN(self.code, mo.end())
        self.tokens.append(Token('EOF', '', line_num, len(self.code) - line_start))
