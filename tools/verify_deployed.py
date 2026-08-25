"""Verify one explicitly selected Pathfinder Lambda against local code.

The live combat log contains only the specialist's final path. It does not reveal
which Lambda function/alias ran, the raw arguments, or the raw Lambda response,
so identical paths alone cannot diagnose deployment state. This script directly
invokes a named Lambda and compares its version and route with local code.

Usage:
    python tools/verify_deployed.py <lambda-function-name>

Without a name it lists path/route-like candidates, but discovery is not proof
that a candidate is the function attached to the live Pathfinding Specialist.
"""

import json
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "combat-logs"))

from live_board import GRID  # noqa: E402
import pathfinder  # noqa: E402

EXPECTED = pathfinder.TOOL_VERSION


def aws(args):
    completed = subprocess.run(
        ["aws"] + args, capture_output=True, text=True, shell=True
    )
    if completed.returncode != 0:
        return None, (completed.stderr or completed.stdout).strip()[:500]
    return completed.stdout, None


def discover():
    out, err = aws(["lambda", "list-functions", "--query",
                    "Functions[].FunctionName", "--output", "json"])
    if out is None:
        return [], err
    names = json.loads(out)
    hits = [n for n in names if "path" in n.lower() or "route" in n.lower()]
    return hits, None


def invoke(name):
    payload = {
        "game_map": GRID,
        "start_pos": [5, 4],
        "strategy": "max_points",
        "verbose": True,
    }
    handle = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
    out_file = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
    out_file.close()
    try:
        json.dump(payload, handle)
        handle.close()
        _, err = aws([
            "lambda", "invoke",
            "--function-name", name,
            "--payload", f"fileb://{handle.name}",
            "--cli-read-timeout", "120",
            out_file.name,
        ])
        if err:
            return None, err
        with open(out_file.name, encoding="utf-8") as fh:
            raw = fh.read()
    finally:
        os.unlink(handle.name)
        os.unlink(out_file.name)

    try:
        envelope = json.loads(raw)
    except json.JSONDecodeError:
        return None, raw[:500]
    body = envelope.get("body", envelope)
    if isinstance(body, str):
        try:
            body = json.loads(body)
        except json.JSONDecodeError:
            return None, body[:500]
    return body, None


def report(name, body):
    version = body.get("v", "ABSENT")
    coords = [tuple(p) for p in body.get("coords", [])]
    cells = [GRID[r][c] for r, c in coords] if coords else []
    path = body.get("path") or []

    print(f"function                 : {name}")
    print(f"deployed version         : {version}")
    print(f"expected version         : {EXPECTED}")
    print(f"moves                    : {len(path)}")
    print(f"c5 census returned       : {body.get('c5', 'ABSENT')}")
    if cells:
        print(f"red key  (c40) on route  : {'c40' in cells}")
        print(f"red door (c30) on route  : {'c30' in cells}")
        print(f"coins collected          : {cells.count('c7')} of 2")
        print(f"treasure last            : {cells[-1] == 'treasure' if cells else False}")

    print()
    if version == "ABSENT":
        print("VERDICT: UNVERSIONED RESPONSE.")
        print("         This direct target did not return the current live contract.")
        print("         Confirm the exact function/alias attached to the specialist;")
        print("         combat logs alone cannot identify it.")
    elif version != EXPECTED:
        print(f"VERDICT: VERSION MISMATCH. Target returned {version}; local is {EXPECTED}.")
        print("         Confirm the attached function/alias and its published version.")
    else:
        print("VERDICT: DIRECT TARGET MATCHES LOCAL CODE.")
        if cells and not ("c40" in cells and "c30" in cells):
            print("         This exact payload still omits the key/door, so inspect")
            print("         planner input/diagnostics rather than the combat-log relay.")
        else:
            print("         If a scored run still emits 77 moves, inspect CloudWatch:")
            print("         no invocation means wrong attachment/relay; a 97-move result")
            print("         means the specialist replaced or recalled another path.")


def main():
    if len(sys.argv) > 1:
        names = [sys.argv[1]]
    else:
        names, err = discover()
        if err:
            print("could not list functions:", err)
            print("pass the function name explicitly, e.g.")
            print("  python tools/verify_deployed.py AgentCoreGatewayTool-Pathfinding")
            return 2
        if not names:
            print("no Lambda whose name contains 'path' or 'route' was found.")
            return 2
        print("candidate functions:", ", ".join(names), "\n")

    exit_code = 1
    for name in names:
        body, err = invoke(name)
        if err:
            print(f"function                 : {name}")
            print(f"invoke failed            : {err}\n")
            continue
        report(name, body)
        if body.get("v") == EXPECTED:
            exit_code = 0
        print()
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
