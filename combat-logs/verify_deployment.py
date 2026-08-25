"""Compare a combat-log path with the canonical local planner.

The combat log contains only the path relayed to the game, not the Lambda target,
raw invocation, raw response, or version. This script therefore reports route
differences without claiming which deployment layer caused them.
"""

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from live_board import GRID  # noqa: E402
from tools import pathfinder  # noqa: E402

# Captured from the 12:19:00 run before that log file was deleted.
PATH_1219 = ["up","left","left","left","left","up","down","down","up","right",
    "right","right","right","up","right","right","right","right","left","down",
    "down","right","left","down","down","down","left","right","right","up","left",
    "up","up","up","up","up","up","up","left","right","right","down","left","down",
    "down","down","down","down","down","down","left","right","right","up","left",
    "up","up","up","right","right","right","down","down","down","down","up","up",
    "up","up","up","up","down","down","right","right","right","right"]

DELTA = {"up": (-1, 0), "down": (1, 0), "left": (0, -1), "right": (0, 1)}


def live_path(log_path):
    events = json.load(open(log_path, encoding="utf-8"))["events"]
    for event in events:
        if event.get("type") == "AnswerChallenge" and "Pathfinding" in str(event.get("message", "")):
            return json.loads(re.search(r"\[.*\]", event["message"], re.S).group(0))
    raise SystemExit("no navigation answer in log")


def replay(grid, start, moves):
    r, c = start
    seen, illegal = [], []
    for move in moves:
        dr, dc = DELTA[move]
        r, c = r + dr, c + dc
        if not (0 <= r < len(grid) and 0 <= c < len(grid[0])):
            illegal.append(("offgrid", r, c))
            break
        cell = grid[r][c]
        if cell == "wall":
            illegal.append(("wall", r, c))
        if cell not in ("normal", "start", "wall"):
            seen.append((cell, r, c))
        # The engine ends the run as soon as treasure is entered; path suffixes
        # are never executed and must not be reported as wall/off-grid failures.
        if cell == "treasure":
            break
    return seen, illegal, (r, c)


def main():
    log = sys.argv[1]
    relayed = live_path(log)

    print("combat-log moves         :", len(relayed))
    print("12:19 moves              :", len(PATH_1219))
    print("combat-log == 12:19      :", relayed == PATH_1219)

    mine = pathfinder.plan({"game_map": GRID, "strategy": "max_points"})["path"]
    print("canonical moves          :", len(mine))
    print("combat-log == canonical  :", relayed == mine)

    seen, illegal, end = replay(GRID, (5, 4), relayed)
    order = []
    for cell, r, c in seen:
        if (cell, r, c) not in order:
            order.append((cell, r, c))
    print("replay ends at           :", end, "expected treasure (4, 8)")
    print("replay illegal steps     :", illegal)
    print("replay tiles touched     :", [f"{c}@{r},{k}" for c, r, k in order])


if __name__ == "__main__":
    main()
