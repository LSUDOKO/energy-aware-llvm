// Benchmark: longest Collatz chain starting below 30000 (data-dependent loop)
int main() {
    int best = 0;
    for (int start = 1; start < 30000; start++) {
        int n = start;
        int steps = 0;
        while (n != 1) {
            if (n % 2 == 0) {
                n = n / 2;
            } else {
                n = 3 * n + 1;
            }
            steps++;
        }
        if (steps > best) {
            best = steps;
        }
    }
    return best;
}
