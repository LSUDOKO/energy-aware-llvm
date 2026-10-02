from llvmlite import binding

class IRFeatureExtractor:
    def __init__(self, module_ref: binding.ModuleRef):
        self.module = module_ref

    def extract_features(self):
        """
        Extracts static metrics from the LLVM IR module.
        Simulates the '35+ IR Metrics Extractor' from Stage 2.
        """
        features = {
            'total_instructions': 0,
            'num_functions': 0,
            'num_basic_blocks': 0,
            'num_loads': 0,
            'num_stores': 0,
            'num_branches': 0,
            'num_calls': 0,
            'num_alu_ops': 0,
            'num_fp_ops': 0,
            'num_allocas': 0,
            'num_icmp': 0,
            'num_fcmp': 0,
            'cyclomatic_complexity_estimate': 0
        }
        
        # To bypass llvmlite iteration limitations, we analyze the string IR
        ir_str = str(self.module)
        
        features['num_functions'] = ir_str.count('define ')
        # A basic block label usually ends with ':'
        features['num_basic_blocks'] = sum(1 for line in ir_str.split('\n') if line.endswith(':') and not line.startswith(';'))
        
        lines = ir_str.split('\n')
        for line in lines:
            line = line.strip()
            # Skip comments, empty lines, and declarations
            if not line or line.startswith(';') or line.startswith('define') or line.startswith('}') or line.startswith('target '):
                continue
                
            # If it looks like an instruction
            if '=' in line or line.startswith('br ') or line.startswith('ret ') or line.startswith('store ') or line.startswith('call '):
                features['total_instructions'] += 1
            
            if ' load ' in line:
                features['num_loads'] += 1
            elif ' store ' in line:
                features['num_stores'] += 1
            elif ' br ' in line or ' switch ' in line:
                features['num_branches'] += 1
            elif ' call ' in line:
                features['num_calls'] += 1
            elif ' alloca ' in line:
                features['num_allocas'] += 1
            elif ' icmp ' in line:
                features['num_icmp'] += 1
            elif ' fcmp ' in line:
                features['num_fcmp'] += 1
            elif any(op in line for op in [' add ', ' sub ', ' mul ', ' sdiv ', ' udiv ', ' shl ', ' lshr ', ' ashr ', ' and ', ' or ', ' xor ']):
                features['num_alu_ops'] += 1
            elif any(op in line for op in [' fadd ', ' fsub ', ' fmul ', ' fdiv ']):
                features['num_fp_ops'] += 1
                
        # Simple CC estimate = edges - nodes + 2P (approx branches - basic blocks)
        features['cyclomatic_complexity_estimate'] = max(1, features['num_branches'] - features['num_basic_blocks'] + 2 * features['num_functions'])
        
        return features

if __name__ == '__main__':
    print("IRFeatureExtractor ready.")
