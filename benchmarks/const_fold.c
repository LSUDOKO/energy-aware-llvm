// Benchmark: constant-heavy code that rewards online folding (Stage 1)
int mix(int x) {
    int c = 3 * (4 + 5) - 2 * 7;      // folds to 17
    int y = x * 1 + 0;                // identity elimination
    int z = y + c;
    return z;
}

int main() {
    int a = mix(10);
    int b = 64 / 4 / 4;               // folds to 4
    float f = 1.5 * 4.0 + 0.5;        // folds to 6.5
    int acc = 0;
    if (a == 27) {
        acc = acc + b;
    }
    while (f > 6.0) {
        acc = acc + 1;
        f = f - 1.0;
    }
    return acc;
}
