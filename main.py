import sys
from frontend.lexer import Lexer
from frontend.parser import Parser
from frontend.codegen import CodeGenVisitor

def compile_code(source_code):
    print("--- Source Code ---")
    print(source_code)
    
    print("\n--- Lexing ---")
    lexer = Lexer(source_code)
    for token in lexer.tokens:
        print(token)
        
    print("\n--- Parsing ---")
    parser = Parser(lexer.tokens)
    ast = parser.parse()
    print("AST built successfully.")
    
    print("\n--- Code Generation (LLVM IR) ---")
    codegen = CodeGenVisitor()
    module = codegen.generate_code(ast)
    
    print("\n--- Emitted LLVM IR ---")
    print(str(module))

if __name__ == '__main__':
    # Sample MiniC code to test
    sample_code = """
    int main() {
        int x = 5;
        int y = 10;
        int z = x + y * 2;
        
        if (z == 25) {
            return 1;
        } else {
            return 0;
        }
    }
    """
    compile_code(sample_code)
