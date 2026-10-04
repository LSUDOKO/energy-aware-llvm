// Benchmark: polynomial evaluation, Horner scheme (float loop)
// p(x) = 5x^4 - 2x^3 + 3x^2 - x + 7
float poly(float x) {
    float r = 5.0;
    r = r * x - 2.0;
    r = r * x + 3.0;
    r = r * x - 1.0;
    r = r * x + 7.0;
    return r;
}

int main() {
    int hits = 0;
    float x = 0.1;
    int i = 0;
    while (i < 400) {
        float y = poly(x);
        if (y > 6.5) {
            hits = hits + 1;
        }
        x = x + 0.01;
        i = i + 1;
    }
    return hits;
}
