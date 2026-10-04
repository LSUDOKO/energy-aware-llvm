// Benchmark: prime counting with trial division (nested loops + calls)
int is_prime(int n) {
    if (n < 2) {
        return 0;
    }
    int d = 2;
    while (d * d <= n) {
        if (n - (n / d) * d == 0) {
            return 0;
        }
        d = d + 1;
    }
    return 1;
}

int count_primes(int limit) {
    int c = 0;
    int i = 2;
    while (i <= limit) {
        if (is_prime(i) == 1) {
            c = c + 1;
        }
        i = i + 1;
    }
    return c;
}

int main() {
    return count_primes(500);
}
