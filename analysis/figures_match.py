"""The last frame of the UC2 match -> analysis/figures/f11_uc2_match.png.

F11 The frame at tick 1200 (t = 60 s) of the seed-42 match that every UC2 run replays, drawn by
    the game's viewer (tank-arena/viz/match.py, figure style): the map, the six tanks P1 to P6
    (players 0 to 5), the shells and hits of the last 0.6 s, and the side panel with each
    player's points, the match's shots, hits and payments, and the payment rules. The match is
    deterministic, so the frame is the same in every run; the measured runs play without a screen.

Needs pygame, which the analysis does not otherwise use, at the version of the game's viewer
(tank-arena/README.md, tank-arena/watch.sh):

    .venv/bin/pip install pygame==2.6.1
    .venv/bin/python analysis/figures_match.py
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tank-arena"))

from viz.match import UC2_SEED, export_figures, simulate   # noqa: E402

TICK = 1200


def f11_uc2_match():
    match = simulate(UC2_SEED)
    if not match.is_uc2_reference:
        raise SystemExit("seed 42 no longer reproduces the UC2 reference log")
    with tempfile.TemporaryDirectory() as tmp:
        (path,) = export_figures(match, Path(tmp), [TICK])
        out = REPO / "analysis" / "figures" / "f11_uc2_match.png"
        shutil.copyfile(path, out)
    print(f"wrote {os.path.relpath(out, REPO)}")


if __name__ == "__main__":
    f11_uc2_match()
