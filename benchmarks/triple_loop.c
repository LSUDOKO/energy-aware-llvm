// Benchmark: three nested loops counting (i*j + k) % 7 == 0 (~3.4M iterations)
int main() {
    int count = 0;
    for (int i = 1; i <= 150; i++) {
        for (int j = 1; j <= 150; j++) {
            for (int k = 1; k <= 150; k++) {
                if ((i * j + k) % 7 == 0) {
                    count++;
                }
            }
        }
    }
    return count;
}
