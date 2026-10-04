// Benchmark: Euclidean GCD (division-heavy loop)
int gcd(int a, int b) {
    while (b != 0) {
        int r = a - (a / b) * b;   // a % b without a modulo operator
        a = b;
        b = r;
    }
    return a;
}

int main() {
    return gcd(1071, 462);
}
