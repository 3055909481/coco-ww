"""从跑轴记录 (logs/axis_runs/*.jsonl) 量「按下 → 程序在画面上认出变化」要多久。

算法在 src/recorder/latency.py (详细面板也用), 这里只负责读文件和打印。

用法: python tools/axis_latency.py [jsonl 文件或目录 ...]   (默认读 logs/axis_runs 下最新 20 局)
"""
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.recorder.latency import analyse, pct  # noqa: E402

DEFAULT_RUNS = 20


def load(path):
    rows = []
    with open(path, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return rows


def report(name, samples):
    if not samples:
        print(f'{name}: 没有样本')
        return
    up = [s[0] * 1000 for s in samples]
    low = [s[1] * 1000 for s in samples if s[1] is not None]
    presses = [s[2] for s in samples]
    print(f'{name}: {len(samples)} 次')
    print(f'  认出用时 上界  p10 {pct(up, .1):5.0f}ms  p50 {pct(up, .5):5.0f}ms  p90 {pct(up, .9):5.0f}ms')
    if low:
        print(f'  认出用时 下界  p10 {pct(low, .1):5.0f}ms  p50 {pct(low, .5):5.0f}ms  p90 {pct(low, .9):5.0f}ms')
    print(f'  每次按了几下  p50 {pct(presses, .5)}  p90 {pct(presses, .9)}  最多 {max(presses)}')


def main(argv):
    paths = []
    for arg in argv or [os.path.join('logs', 'axis_runs')]:
        if os.path.isdir(arg):
            files = sorted(glob.glob(os.path.join(arg, '*.jsonl')))
            paths.extend(files if argv else files[-DEFAULT_RUNS:])
        else:
            paths.append(arg)
    merged = {'switch': [], 'lib': []}
    for path in paths:
        result = analyse(load(path))
        for k in merged:
            merged[k].extend(result[k])
    print(f'{len(paths)} 局')
    report('切人 (按 1/2/3 → 队伍栏换人)', merged['switch'])
    report('大招 (按 R → HUD 消失)', merged['lib'])


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    main(sys.argv[1:])
