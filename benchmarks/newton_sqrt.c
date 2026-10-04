// Benchmark: Newton-Raphson square roots (float iteration + branch)
float mysqrt(float x) {
    float g = x;
    int i = 0;
    while (i < 24) {
        g = 0.5 * (g + x / g);
        i = i + 1;
    }
    return g;
}

int main() {
    int hits = 0;
    float v = 1.0;
    while (v <= 100.0) {
        float r = mysqrt(v);
        if (r * r > v * 0.999) {
            if (r * r < v * 1.001) {
                hits = hits + 1;
            }
        }
        v = v + 1.0;
    }
    return hits;
}
