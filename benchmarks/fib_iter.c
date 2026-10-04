// Benchmark: iterative Fibonacci (loop + integer arithmetic)
int fib(int n) {
    int a = 0;
    int b = 1;
    int i = 0;
    while (i < n) {
        int t = a + b;
        a = b;
        b = t;
        i = i + 1;
    }
    return a;
}

int main() {
    return fib(30);
}
