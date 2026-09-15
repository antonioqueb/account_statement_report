"""Parser-only benchmark; never claims database or end-to-end import performance."""
import argparse
import json
import platform
import time
import tracemalloc
from test_add_offline import cfdi, fx


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--count', type=int, default=1000)
    args = parser.parse_args()
    samples = fx.references()
    tracemalloc.start()
    start = time.perf_counter()
    size = 0
    for i in range(args.count):
        raw = samples[i % len(samples)]
        cfdi.parse(raw, fx.COMPANY, 'received')
        size += len(raw)
    elapsed = time.perf_counter() - start
    _, peak = tracemalloc.get_traced_memory()
    print(json.dumps(dict(scope='parser únicamente; ocho fixtures sintéticos repetidos, sin ORM ni PostgreSQL',
                         system=platform.platform(), processor=platform.machine(), python=platform.python_version(),
                         count=args.count, input_bytes=size, elapsed_seconds=round(elapsed, 4),
                         documents_per_second=round(args.count / elapsed, 2), peak_python_bytes=peak), indent=2, ensure_ascii=False))
