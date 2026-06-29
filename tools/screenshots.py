#!/usr/bin/env python3
"""Generate SVG 'terminal screenshots' of the World Cup Terminal for the README.

These are exported straight from the real app via rich's SVG recorder, so the
phosphor colors, flags, dot-matrix scoreboard and GOAL burst are exactly what a
user sees. Output lands in ../docs/*.svg.

    python tools/screenshots.py
"""
import os, sys, importlib.util

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DOCS = os.path.join(ROOT, "docs")
sys.path.insert(0, ROOT)

# import worldcup.py as a module without running main()
spec = importlib.util.spec_from_file_location("worldcup", os.path.join(ROOT, "worldcup.py"))
wc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wc)

from rich.console import Console

WIDTH, HEIGHT = 118, 40
RETRO_THEME = None  # use rich default terminal theme (dark, true-color)


def advance(store, target_minute):
    """Drive the scripted demo forward until its clock reaches target_minute."""
    store._pull_demo()                      # build the slate (clock -> -3)
    # _demo_clock starts at -4 and +1 per call; mins==_demo_clock after increment
    while store._demo_clock < target_minute:
        store._pull_demo()


def capture(name, title, minute, tick):
    store = wc.Store(demo=True)
    advance(store, minute)
    console = Console(record=True, width=WIDTH, height=HEIGHT, file=open(os.devnull, "w"))
    console.print(wc.build_layout(store, tick))
    out = os.path.join(DOCS, name)
    console.save_svg(out, title=title)
    console.file.close()
    print("wrote", os.path.relpath(out, ROOT))


def main():
    os.makedirs(DOCS, exist_ok=True)
    # Hero: the 41' Rodrygo goal — full GOAL burst, board reads BRA 3 - JPN 0,
    # incident log populated, r/soccer buzz live on the right rail.
    capture("hero.svg", "WORLD CUP TERMINAL  ::  retro phosphor match-cast",
            minute=41, tick=4)
    # Live, calm beat: mid-match incident log without an active burst.
    capture("live.svg", "WORLD CUP TERMINAL  ::  live match-cast",
            minute=48, tick=120)
    # Pre-match: scoreboard countdown + 'waiting for kick-off' + fixtures slate.
    capture("prematch.svg", "WORLD CUP TERMINAL  ::  matchday slate",
            minute=0, tick=2)


if __name__ == "__main__":
    main()
