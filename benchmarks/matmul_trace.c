// Benchmark: matrix diagonal trace via index arithmetic (no arrays)
// C[i][i] = sum_k A[i][k] * B[k][i],  A[i][k] = (3i + k) % 7,
// B[k][i] = (5k + i) % 5,  N = 16
int main() {
    int N = 16;
    int total = 0;
    int i = 0;
    while (i < N) {
        int s = 0;
        int k = 0;
        while (k < N) {
            int a = i * 3 + k;
            a = a - (a / 7) * 7;
            int b = k * 5 + i;
            b = b - (b / 5) * 5;
            s = s + a * b;
            k = k + 1;
        }
        total = total + s;
        i = i + 1;
    }
    return total;
}
