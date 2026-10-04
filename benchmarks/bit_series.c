// Benchmark: bit-series (popcount via division, no bitwise operators)
int popcount(int x) {
    int c = 0;
    while (x != 0) {
        if (x - (x / 2) * 2 == 1) {
            c = c + 1;
        }
        x = x / 2;
    }
    return c;
}

int main() {
    int total = 0;
    int i = 1;
    while (i < 300) {
        total = total + popcount(i) * (i - (i / 3) * 3);
        i = i + 1;
    }
    return total;
}
