// Benchmark: Mandelbrot escape-time over a coarse grid (float + branches)
int escape(float cx, float cy) {
    float x = 0.0;
    float y = 0.0;
    int it = 0;
    while (it < 30) {
        float xt = x * x - y * y + cx;
        y = 2.0 * x * y + cy;
        x = xt;
        if (x * x + y * y > 4.0) {
            return it + 1;
        }
        it = it + 1;
    }
    return 30;
}

int main() {
    int total = 0;
    int i = 0;
    while (i < 30) {
        int j = 0;
        while (j < 24) {
            float cx = -2.0 + i * 0.1;
            float cy = -1.2 + j * 0.1;
            total = total + escape(cx, cy);
            j = j + 1;
        }
        i = i + 1;
    }
    return total;
}
