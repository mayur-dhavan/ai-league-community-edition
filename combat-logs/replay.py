"""Replay an obsolete, partial board reconstruction against the planner.

WARNING: this is NOT the exact August 25 live board. It was inferred from older
planner notes, conflicts with coordinates traversed in the latest event logs,
and omits known tile types including c30/c4. Use it only as a planner smoke test.
Replace GRID with the exact array captured from a navigation prompt before using
its score or route to make a live-game decision.

Usage:  python combat-logs/replay.py
"""

import contextlib
import io
import json
import os
import sys

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"
))

import pathfinder  # noqa: E402


def _quiet_plan(body):
    """Run the planner without its CloudWatch diagnostics line."""
    with contextlib.redirect_stdout(io.StringIO()):
        return pathfinder.plan(body)

# 9x9. Positions taken from the logged notes; unknown cells are walkable floor.
#            col 0     1        2        3        4        5        6        7        8
GRID = [
    ["c3",    "normal", "c3",    "normal", "normal", "normal", "c18",   "normal", "c18"],
    ["normal", "wall",  "wall",  "normal", "c7",     "wall",   "wall",  "wall",   "normal"],
    ["normal", "c1",    "normal", "wall",  "normal", "normal", "normal", "c1",    "normal"],
    ["c5",    "wall",   "normal", "wall",  "normal", "wall",   "wall",  "wall",   "c5"],
    ["c7",    "normal", "normal", "normal", "normal", "normal", "normal", "normal", "treasure"],
    ["c5",    "wall",   "wall",  "wall",   "start",  "wall",   "wall",  "wall",   "c5"],
    ["normal", "c1",    "normal", "normal", "c8",    "normal", "normal", "c1",    "normal"],
    ["normal", "wall",  "c8",    "wall",   "normal", "wall",   "c8",    "wall",   "normal"],
    ["normal", "normal", "normal", "normal", "c40",  "normal", "c2",    "normal", "c2"],
]

START = [5, 4]

TILE_POINTS = {
    "c1": 400, "c2": 750, "c3": 750, "c4": 750, "c5": 250,
    "c7": 2500, "c18": 600, "c30": 1000, "c40": 50,
}


def theoretical_max(grid):
    counts = {}
    for row in grid:
        for cell in row:
            counts[cell] = counts.get(cell, 0) + 1
    total = 3000 + 1250 + 1000  # treasure + all lives + perfect token bonus
    breakdown = []
    for tile, points in sorted(TILE_POINTS.items()):
        n = counts.get(tile, 0)
        if n:
            total += n * points
            breakdown.append(f"{tile}x{n}={n * points}")
    return total, breakdown, counts


def run(label, **body):
    payload = {"game_map": GRID, "start_pos": START, "verbose": True}
    payload.update(body)
    plan = _quiet_plan(payload)
    quiet = _quiet_plan({**payload, "verbose": False})
    payload_chars = len(json.dumps(quiet, separators=(",", ":")))
    print(
        f"{label:<26} score={plan['expected_score']:>9.0f}  "
        f"pts={plan['expected_points']:>7.0f}  "
        f"lives={plan['expected_lives_remaining']:>4.1f}  "
        f"challenges={plan['challenges_attempted']:>2}  "
        f"steps={plan['steps']:>3}  "
        f"secs={plan['estimated_seconds']:>3}  "
        f"treasure={'Y' if plan['reaches_treasure'] else 'N'}  "
        f"payload={payload_chars}ch"
    )
    return plan


def main():
    print("WARNING: GRID is an obsolete partial reconstruction, not the latest live board")
    total, breakdown, counts = theoretical_max(GRID)
    print("tile census:", " ".join(f"{k}:{v}" for k, v in sorted(counts.items())
                                   if k not in ("normal", "wall", "start")))
    print("theoretical max:", total, "|", " ".join(breakdown))
    print("actual run 2 (max_points, old planner): 4050\n")

    run("safe", strategy="safe")
    run("get_coins", strategy="get_coins")
    run("no_door", strategy="no_door")
    plan = run("max_points", strategy="max_points")
    run("max_loot", strategy="max_loot")
    run("max_points +door", strategy="max_points",
        challenge_confidence={"c30": 0.99, "c40": 0.99})

    print("\nmax_points notes:")
    for note in plan["notes"]:
        print("  -", note)
    print("\nmax_points visits:", ", ".join(plan["visited_tiles"]) or "(none)")
    submission = _quiet_plan(
        {"game_map": GRID, "start_pos": START, "strategy": "max_points"}
    )
    blob = json.dumps(submission, separators=(",", ":"))
    print(f"\nsubmission payload: {len(blob)} chars, ~{len(blob) // 4} tokens")
    print("  keys:", ", ".join(submission))


if __name__ == "__main__":
    main()
