int main() {
    int i = 0;
    int sum = 0;
    while (i < 1000) {
        sum = sum + i;
        i = i + 1;
    }
    if (sum > 500) {
        return 1;
    }
    return 0;
}
