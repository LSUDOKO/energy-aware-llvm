// Benchmark: longest Collatz sequence below 1000 (branchy loop + calls)
int collatz_steps(int n) {
    int steps = 0;
    while (n != 1) {
        if (n - (n / 2) * 2 == 0) {
            n = n / 2;
        } else {
            n = 3 * n + 1;
        }
        steps = steps + 1;
    }
    return steps;
}

int main() {
    int max = 0;
    int i = 1;
    while (i < 1000) {
        int s = collatz_steps(i);
        if (s > max) {
            max = s;
        }
        i = i + 1;
    }
    return max;
}
