#!/usr/bin/env python
"""Run a command and render its real output as a terminal-style PNG.

The image is drawn from the captured stdout/stderr (nothing is typed in by
hand), with the command line shown as the prompt.  Used for the README's
CLI screenshots.

    ./venv/bin/python tools/terminal_shot.py out.png -- compiler_driver.py benchmarks/fib_rec.c -Mperf
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
import textwrap
from pathlib import Path

import matplotlib
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
FONT = Path(matplotlib.get_data_path()) / "fonts" / "ttf" / "DejaVuSansMono.ttf"
ANSI = re.compile(r"\x1b\[[0-9;]*m")

BG, FG, DIM = (16, 24, 32), (217, 226, 236), (122, 140, 158)
PROMPT, GOOD, BAD = (227, 155, 11), (84, 200, 150), (240, 110, 100)


def colour(line: str):
    low = line.lower()
    if "failed" in low or "error" in low or "incorrect" in low:
        return BAD
    if "passed" in low or "successful" in low or "verified" in low:
        return GOOD
    return FG


def render(command: str, output: str, path: Path, width_chars: int = 118,
           max_lines: int = 60, size: int = 15) -> None:
    font = ImageFont.truetype(str(FONT), size)
    lines: list[tuple[str, tuple]] = [(f"$ {command}", PROMPT)]
    for raw in ANSI.sub("", output).splitlines():
        wrapped = textwrap.wrap(raw, width_chars, subsequent_indent="    ") or [""]
        lines.extend((w, colour(raw)) for w in wrapped)
    if len(lines) > max_lines + 1:
        keep_head, keep_tail = max_lines // 3, max_lines - max_lines // 3
        hidden = len(lines) - keep_head - keep_tail
        lines = lines[:keep_head] + [(f"... {hidden} lines omitted ...", DIM)] + lines[-keep_tail:]
    lh = size + 6
    w = int(font.getlength("M") * (width_chars + 4)) + 24
    h = lh * len(lines) + 56
    img = Image.new("RGB", (w, h), BG)
    d = ImageDraw.Draw(img)
    for i, c in enumerate(((255, 95, 86), (255, 189, 46), (39, 201, 63))):
        d.ellipse((14 + i * 22, 14, 26 + i * 22, 26), fill=c)
    y = 42
    for text, col in lines:
        d.text((16, y), text, font=font, fill=col)
        y += lh
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("out", type=Path)
    ap.add_argument("--max-lines", type=int, default=60)
    if "--" not in sys.argv:
        ap.error("give the command after --")
    split = sys.argv.index("--")
    args = ap.parse_args(sys.argv[1:split])
    cmd = sys.argv[split + 1:]
    if not cmd:
        ap.error("give the command after --")
    proc = subprocess.run([sys.executable, *cmd], cwd=ROOT, capture_output=True,
                          text=True)
    shown = "python " + " ".join(cmd)
    render(shown, proc.stdout + proc.stderr, args.out, max_lines=args.max_lines)
    print(f"wrote {args.out} (exit status {proc.returncode})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
