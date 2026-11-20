# -*- coding: utf-8 -*-
"""启动耗时基准（2.3 M1）—— 同机同比 onefile / onedir，非运行时代码。

    python tools/bench_startup.py dist\\AppDataCleaner.exe
    python tools/bench_startup.py dist\\AppDataCleaner\\AppDataCleaner.exe --runs 5

口径：每次以 `<exe> --version` 冷启一次并计时（不启窗口、不起服务），取中位数。
只读目标 exe、不写任何文件；结论仅用于同机先后对比，不做跨机判定。
"""

import argparse
import statistics
import subprocess
import sys
import time


def measure(exe, runs):
    times = []
    for _ in range(runs):
        start = time.perf_counter()
        r = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=180)
        elapsed = time.perf_counter() - start
        if r.returncode != 0:
            raise SystemExit("FAIL: %s --version 退出码 %d" % (exe, r.returncode))
        times.append(elapsed)
    return times


def main(argv=None):
    ap = argparse.ArgumentParser(description="启动耗时基准（--version 口径）")
    ap.add_argument("exe", help="待测 exe 的路径")
    ap.add_argument("--runs", type=int, default=5, help="测量次数（默认 5）")
    args = ap.parse_args(argv)
    if args.runs < 3:
        raise SystemExit("FAIL: --runs 至少 3 次")

    times = measure(args.exe, args.runs)
    ms = [round(t * 1000) for t in times]
    print("exe    : %s" % args.exe)
    print("runs   : %s" % " / ".join(str(x) + " ms" for x in ms))
    print("median : %d ms" % round(statistics.median(times) * 1000))
    return 0


if __name__ == "__main__":
    sys.exit(main())
