// Benchmark: naive recursive Fibonacci (function-call heavy)
int fib(int n) {
    if (n < 2) {
        return n;
    }
    return fib(n - 1) + fib(n - 2);
}

int main() {
    return fib(21);
}
