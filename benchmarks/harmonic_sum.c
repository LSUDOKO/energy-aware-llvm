// Benchmark: single-precision harmonic series (float accumulate, for loop)
int main() {
    float s = 0.0;
    for (int k = 1; k <= 400000; k++) {
        s += 1.0 / k;
    }
    int scaled = s * 1000.0;   // explicit float->int narrowing
    return scaled;
}
