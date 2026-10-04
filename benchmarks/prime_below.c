// Benchmark: count primes below 30000 by trial division (nested loops)
int main() {
    int count = 0;
    for (int n = 2; n < 30000; n++) {
        int prime = 1;
        for (int d = 2; d * d <= n; d++) {
            if (n % d == 0) {
                prime = 0;
                d = n;
            }
        }
        count += prime;
    }
    return count;
}
