/* Timing harness linked against a compiled MiniC object.
 *
 * The object's `main` is renamed to `minic_main` (objcopy) so this file can
 * provide the real entry point.  It calls the program N times and prints
 *     <return value> <seconds per call>
 * so the measurement covers only native execution of the generated code
 * (no JIT, no interpreter, no Python in the loop).
 */
#include <stdio.h>
#include <stdlib.h>
#include <time.h>

extern int minic_main(void);

static double now(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (double)ts.tv_sec + (double)ts.tv_nsec * 1e-9;
}

int main(int argc, char **argv) {
    long n = argc > 1 ? atol(argv[1]) : 1;
    volatile int sink = 0;
    double t0 = now();
    for (long i = 0; i < n; i++) {
        sink = minic_main();
    }
    double t1 = now();
    printf("%d %.12e\n", (int)sink, (t1 - t0) / (double)n);
    return 0;
}
