// Benchmark: linear-congruential generator, digit sum (for loop, int, ~ms)
int main() {
    int x = 12345;
    int s = 0;
    for (int i = 0; i < 2000000; i++) {
        x = (x * 75 + 74) % 65537;
        s += x % 10;
    }
    return s;
}
