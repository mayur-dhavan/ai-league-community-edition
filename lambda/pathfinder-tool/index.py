import json
from collections import deque
from typing import Dict, List, Optional, Set, Tuple

# ---------------------------------------------------------------------------
# Game constants — mirrored from game_runner.py DEFAULT_TILE_CONFIG
# ---------------------------------------------------------------------------

DIRECTIONS = [(-1, 0, "up"), (1, 0, "down"), (0, -1, "left"), (0, 1, "right")]

DEFAULT_TILE_CONFIG: Dict[str, Dict[str, int]] = {
    "c1": {"points": 400, "damage": 1},
    "c2": {"points": 600, "damage": 1},
    "c3": {"points": 550, "damage": 1},
    "c4": {"points": 800, "damage": 1},
    "c5": {"points": 250, "damage": 1},
    "c6": {"points": 1000, "damage": 2},
    "c7": {"points": 250, "damage": 0},
    "c8": {"points": 0, "damage": 1},
    "c17": {"points": 750, "damage": 2},
    "c18": {"points": 500, "damage": 1},
    "c30": {"points": 1000, "damage": 5},
    "c31": {"points": 1000, "damage": 5},
    "c32": {"points": 1000, "damage": 5},
    "c33": {"points": 1000, "damage": 5},
    "c40": {"points": 50, "damage": 0},
    "c41": {"points": 50, "damage": 0},
    "c42": {"points": 50, "damage": 0},
    "c43": {"points": 50, "damage": 0},
}

COIN_CELLS = {"c7"}
SPIKE_CELLS = {"c8"}
CHALLENGE_CELLS = {"c1", "c2", "c3", "c4", "c5", "c6", "c17", "c18"}
KEY_CELLS = {"c40", "c41", "c42", "c43"}
DOOR_CELLS = {"c30", "c31", "c32", "c33"}
KEY_TO_DOOR = {"c40": "c30", "c41": "c31", "c42": "c32", "c43": "c33"}
LOOT_CELLS = COIN_CELLS | CHALLENGE_CELLS | KEY_CELLS
VALUABLE_CELLS = COIN_CELLS | CHALLENGE_CELLS | KEY_CELLS | DOOR_CELLS

LIFE_VALUE = 250  # each remaining life is worth 250 pts in final score


def lambda_handler(event, context):
    """
    AWS Lambda Pathfinder — Score-maximising navigation engine.

    Strategies:
      swift      — BFS shortest path to treasure (fewest steps)
      get_coins  — Greedily collect c7 coins then reach treasure
      smart_loot — Visit all loot ranked by value/distance, avoid spikes, respect keys
      max_score  — Full optimizer: net-value routing (points - damage*250), life-aware

    ## Map Definitions
        - "wall": non-walkable cell
        - "treasure": target cell to reach
        - "normal": walkable cell with no special properties
        - "start": the start cell of your avatar, acts as normal cell
        - "c1"-"c6": challenge tiles
        - "c7": Coins (passive points, no challenge)
        - "c8": Spikes (passive damage, AVOID)
        - "c17","c18": advanced challenge tiles
        - "c30"-"c33": Door tiles (need matching key c40-c43)
        - "c40"-"c43": Key tiles (unlock matching door)

    Usage: Use strategy "max_score" for highest scoring path
    """
    try:
        # Parse input — support API Gateway, AgentCore MCP, and direct invocation
        if "body" in event:
            body = json.loads(event["body"]) if isinstance(event["body"], str) else event["body"]
        else:
            body = event

        game_map = body.get("game_map", [])
        start_pos = body.get("start_pos", [0, 0])
        strategy = body.get("strategy", "max_score")
        lives = body.get("lives", 5)
        tile_overrides = body.get("tile_overrides", {})
        print(f"DEBUG: start_pos={start_pos} strategy={strategy} grid_size={len(str(game_map))} chars")

        # --- Robust game_map parsing ---
        map_object = None
        if isinstance(game_map, str):
            game_map = game_map.strip()
            lo = next((i for i, ch in enumerate(game_map) if ch in "[{"), -1)
            hi = next((i for i in range(len(game_map) - 1, -1, -1) if game_map[i] in "]}"), -1)
            if lo >= 0 and hi > lo:
                game_map = game_map[lo:hi + 1]
            try:
                game_map = json.loads(game_map)
            except json.JSONDecodeError:
                return _err(400, f"game_map is not valid JSON: {game_map[:100]}")

        if isinstance(game_map, dict):
            map_object = game_map
            game_map = (
                map_object.get("grid")
                or map_object.get("game_map")
                or map_object.get("map", [])
            )

        if isinstance(start_pos, str):
            try:
                start_pos = json.loads(start_pos)
            except json.JSONDecodeError:
                start_pos = [0, 0]

        if not game_map or not isinstance(game_map, list):
            return _err(400, "Missing or invalid game_map")

        rows, cols = len(game_map), len(game_map[0])

        # Extract playerStart from map object if available
        if map_object and "playerStart" in map_object:
            ps = map_object["playerStart"]
            if isinstance(ps, dict) and "row" in ps and "col" in ps:
                start_pos = [int(ps["row"]), int(ps["col"])]

        # Auto-detect start position from "start" cell in grid
        for r in range(rows):
            for c in range(cols):
                if game_map[r][c] == "start":
                    start_pos = [r, c]
                    break

        # Find treasure
        treasure = None
        for r in range(rows):
            for c in range(cols):
                if game_map[r][c] == "treasure":
                    treasure = (r, c)
                    break
            if treasure:
                break

        if not treasure:
            return _err(400, "No treasure found on map")

        start = tuple(start_pos)

        # Build effective tile config (apply overrides)
        tile_cfg = {}
        for cell_type, defaults in DEFAULT_TILE_CONFIG.items():
            override = tile_overrides.get(cell_type, {})
            tile_cfg[cell_type] = {
                "points": override.get("points", defaults["points"]),
                "damage": override.get("damage", defaults["damage"]),
            }

        print(f"DEBUG: strategy={strategy} start={start} grid={rows}x{cols} lives={lives}")

        # Execute strategy
        if strategy == "max_score":
            path, est_pts, est_dmg = max_score_path(game_map, rows, cols, start, treasure, lives, tile_cfg)
        elif strategy == "smart_loot":
            path, est_pts, est_dmg = smart_loot_path(game_map, rows, cols, start, treasure, lives, tile_cfg)
        elif strategy == "get_coins":
            path = get_coins_path(game_map, rows, cols, start, treasure)
            est_pts, est_dmg = _estimate_path_value(game_map, path, start, tile_cfg)
        else:
            path = swift_path(game_map, rows, cols, start, treasure)
            est_pts, est_dmg = _estimate_path_value(game_map, path, start, tile_cfg)

        result = {
            "path": path,
            "steps": len(path),
            "start_position": list(start),
            "strategy_used": strategy,
            "estimated_points": est_pts,
            "estimated_damage": est_dmg,
        }
        print(f"RESULT: strategy={strategy} steps={len(path)} est_pts={est_pts} est_dmg={est_dmg}")
        return {"statusCode": 200, "body": json.dumps(result)}

    except Exception as e:
        print(f"ERROR: {e}")
        return _err(500, str(e))


def _err(code: int, msg: str):
    return {"statusCode": code, "body": json.dumps({"error": msg})}


# ---------------------------------------------------------------------------
# Core BFS
# ---------------------------------------------------------------------------


def _bfs(
    game_map, rows: int, cols: int, start: Tuple, goal: Tuple,
    avoid_cells: Set[str] = None,
    blocked_doors: Set[str] = None,
) -> Optional[List[str]]:
    """BFS shortest path. Can avoid specific cell types and locked doors."""
    avoid_cells = avoid_cells or set()
    blocked_doors = blocked_doors or set()
    queue = deque([(start[0], start[1], [])])
    visited = {(start[0], start[1])}
    while queue:
        r, c, path = queue.popleft()
        if (r, c) == goal:
            return path
        for dr, dc, move in DIRECTIONS:
            nr, nc = r + dr, c + dc
            if 0 <= nr < rows and 0 <= nc < cols and (nr, nc) not in visited:
                cell = game_map[nr][nc]
                if cell == "wall":
                    continue
                if cell in avoid_cells:
                    continue
                if cell in blocked_doors:
                    continue
                visited.add((nr, nc))
                queue.append((nr, nc, path + [move]))
    return None


# ---------------------------------------------------------------------------
# Strategy: swift
# ---------------------------------------------------------------------------


def swift_path(game_map, rows, cols, start, treasure) -> List[str]:
    """BFS shortest path to treasure — avoids spikes."""
    path = _bfs(game_map, rows, cols, start, treasure, avoid_cells=SPIKE_CELLS)
    if path is not None:
        return path
    # Fallback: allow spikes if no spike-free path exists
    return _bfs(game_map, rows, cols, start, treasure) or []


# ---------------------------------------------------------------------------
# Strategy: get_coins
# ---------------------------------------------------------------------------


def get_coins_path(game_map, rows, cols, start, treasure) -> List[str]:
    """Greedily collect nearby c7 coins (avoiding spikes), then to treasure."""
    board = [row[:] for row in game_map]
    r, c = start
    full_path = []

    for _ in range(50):
        queue = deque([(r, c, [])])
        visited = {(r, c)}
        targets = []
        while queue:
            cr, cc, p = queue.popleft()
            if board[cr][cc] in COIN_CELLS and (cr, cc) != (r, c):
                targets.append((len(p), p, cr, cc))
            for dr, dc, move in DIRECTIONS:
                nr, nc = cr + dr, cc + dc
                if 0 <= nr < rows and 0 <= nc < cols and (nr, nc) not in visited:
                    ncell = board[nr][nc]
                    if ncell == "wall" or ncell in SPIKE_CELLS:
                        continue
                    visited.add((nr, nc))
                    queue.append((nr, nc, p + [move]))
        if not targets:
            break
        targets.sort()
        _, path_to, r, c = targets[0]
        full_path.extend(path_to)
        board[r][c] = "normal"

    path_end = _bfs(board, rows, cols, (r, c), treasure, avoid_cells=SPIKE_CELLS)
    if path_end is not None:
        full_path.extend(path_end)
        return full_path
    return swift_path(game_map, rows, cols, start, treasure)


# ---------------------------------------------------------------------------
# Strategy: smart_loot
# ---------------------------------------------------------------------------


def smart_loot_path(
    game_map, rows, cols, start, treasure, lives: int, tile_cfg: Dict
) -> Tuple[List[str], int, int]:
    """
    Visit all valuable tiles ranked by points/distance.
    Respects key→door ordering, avoids spikes, tracks life budget.
    """
    board = [row[:] for row in game_map]
    r, c = start
    full_path = []
    collected_keys: Set[str] = set()
    total_points = 0
    total_damage = 0
    current_lives = lives

    for _ in range(150):
        locked = DOOR_CELLS - collected_keys
        queue = deque([(r, c, [])])
        visited = {(r, c)}
        targets = []

        while queue:
            cr, cc, p = queue.popleft()
            cell = board[cr][cc]

            if (cr, cc) != (r, c):
                cfg = tile_cfg.get(cell, {"points": 0, "damage": 0})
                pts = cfg["points"]
                dmg = cfg["damage"]
                dist = max(len(p), 1)

                if dmg >= current_lives:
                    pass
                elif cell in KEY_CELLS:
                    targets.append((-1000, dist, cr, cc, list(p), cell))
                elif cell in COIN_CELLS:
                    targets.append((dist, dist, cr, cc, list(p), cell))
                elif cell in CHALLENGE_CELLS and pts > dmg * LIFE_VALUE:
                    priority = dist * 250.0 / max(pts, 1)
                    targets.append((priority, dist, cr, cc, list(p), cell))
                elif cell in DOOR_CELLS and cell in collected_keys:
                    priority = dist * 250.0 / max(pts, 1)
                    targets.append((priority, dist, cr, cc, list(p), cell))

            for dr, dc, move in DIRECTIONS:
                nr, nc = cr + dr, cc + dc
                if 0 <= nr < rows and 0 <= nc < cols and (nr, nc) not in visited:
                    ncell = board[nr][nc]
                    if ncell == "wall" or ncell in SPIKE_CELLS or ncell in locked:
                        continue
                    visited.add((nr, nc))
                    queue.append((nr, nc, p + [move]))

        if not targets:
            break

        targets.sort(key=lambda x: (x[0], x[1]))
        _, _, r, c, path_to, target_cell = targets[0]
        full_path.extend(path_to)

        cfg = tile_cfg.get(target_cell, {"points": 0, "damage": 0})
        total_points += cfg["points"]
        total_damage += cfg["damage"]
        current_lives -= cfg["damage"]
        board[r][c] = "normal"

        if target_cell in KEY_CELLS:
            collected_keys.add(KEY_TO_DOOR[target_cell])

    # Final path to treasure
    locked = DOOR_CELLS - collected_keys
    path_end = _bfs(board, rows, cols, (r, c), treasure,
                    avoid_cells=SPIKE_CELLS, blocked_doors=locked)
    if path_end is not None:
        full_path.extend(path_end)
    else:
        path_end = _bfs(board, rows, cols, (r, c), treasure)
        if path_end:
            full_path.extend(path_end)
        else:
            full_path = swift_path(game_map, rows, cols, start, treasure)
            total_points, total_damage = _estimate_path_value(game_map, full_path, start, tile_cfg)

    return full_path, total_points, total_damage


# ---------------------------------------------------------------------------
# Strategy: max_score (full optimizer)
# ---------------------------------------------------------------------------


def max_score_path(
    game_map, rows, cols, start, treasure, lives: int, tile_cfg: Dict
) -> Tuple[List[str], int, int]:
    """
    Maximum-score strategy using net-value economics.

    Each life = 250 pts at end. A tile is worth visiting only if:
        tile_points > tile_damage × 250

    Priority order:
    1. Keys (free, unlock 1000-pt doors)
    2. Coins (free 250 pts)
    3. High net-value challenges (c4=+550, c6=+500, c2=+350, c3=+300, c17=+250)
    4. Unlocked doors (net +1000 - 5*250 = -250, but if challenge is answered correctly
       we get 1000 pts; skip if too risky with low lives)
    5. Treasure
    """
    board = [row[:] for row in game_map]
    r, c = start
    full_path = []
    collected_keys: Set[str] = set()
    total_points = 0
    total_damage = 0
    current_lives = lives

    for _ in range(200):
        locked = DOOR_CELLS - collected_keys
        queue = deque([(r, c, [])])
        visited = {(r, c)}
        targets = []

        while queue:
            cr, cc, p = queue.popleft()
            cell = board[cr][cc]

            if (cr, cc) != (r, c):
                cfg = tile_cfg.get(cell, {"points": 0, "damage": 0})
                pts = cfg["points"]
                dmg = cfg["damage"]
                dist = max(len(p), 1)
                net_value = pts - (dmg * LIFE_VALUE)

                # Must survive the tile
                if dmg >= current_lives:
                    pass
                elif cell in KEY_CELLS:
                    # Always collect keys — they're free and unlock doors
                    priority = -10000 + dist
                    targets.append((priority, dist, cr, cc, list(p), cell))
                elif cell in COIN_CELLS:
                    # Always collect coins — free 250 pts
                    priority = dist
                    targets.append((priority, dist, cr, cc, list(p), cell))
                elif cell in CHALLENGE_CELLS:
                    # Visit only if positive net value
                    if net_value > 0:
                        priority = dist * 100.0 / max(net_value, 1)
                        targets.append((priority, dist, cr, cc, list(p), cell))
                elif cell in DOOR_CELLS and cell in collected_keys:
                    # Unlocked doors: if we answer correctly we get pts with NO damage.
                    # Only skip if we can't survive a wrong answer.
                    if current_lives > dmg + 1:
                        priority = dist * 100.0 / max(pts, 1)
                        targets.append((priority, dist, cr, cc, list(p), cell))

            for dr, dc, move in DIRECTIONS:
                nr, nc = cr + dr, cc + dc
                if 0 <= nr < rows and 0 <= nc < cols and (nr, nc) not in visited:
                    ncell = board[nr][nc]
                    if ncell == "wall" or ncell in SPIKE_CELLS or ncell in locked:
                        continue
                    visited.add((nr, nc))
                    queue.append((nr, nc, p + [move]))

        if not targets:
            break

        targets.sort(key=lambda x: (x[0], x[1]))
        _, _, r, c, path_to, target_cell = targets[0]
        full_path.extend(path_to)

        cfg = tile_cfg.get(target_cell, {"points": 0, "damage": 0})
        total_points += cfg["points"]
        total_damage += cfg["damage"]
        current_lives -= cfg["damage"]
        board[r][c] = "normal"

        if target_cell in KEY_CELLS:
            collected_keys.add(KEY_TO_DOOR[target_cell])

    # Final path to treasure (spike-free)
    locked = DOOR_CELLS - collected_keys
    path_end = _bfs(board, rows, cols, (r, c), treasure,
                    avoid_cells=SPIKE_CELLS, blocked_doors=locked)
    if path_end is not None:
        full_path.extend(path_end)
    else:
        # Allow spikes if needed (treasure = 1000 pts, worth a few spike hits)
        path_end = _bfs(board, rows, cols, (r, c), treasure, blocked_doors=locked)
        if path_end:
            full_path.extend(path_end)
        else:
            path_end = _bfs(board, rows, cols, (r, c), treasure)
            if path_end:
                full_path.extend(path_end)
            else:
                full_path = swift_path(game_map, rows, cols, start, treasure)
                total_points, total_damage = _estimate_path_value(
                    game_map, full_path, start, tile_cfg
                )

    return full_path, total_points, total_damage


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------


def _estimate_path_value(
    game_map, path: List[str], start: Tuple, tile_cfg: Dict
) -> Tuple[int, int]:
    """Walk a path and estimate total points/damage from tiles visited."""
    r, c = start
    points = 0
    damage = 0
    visited = set()

    for move in path:
        if move == "up":
            r -= 1
        elif move == "down":
            r += 1
        elif move == "left":
            c -= 1
        elif move == "right":
            c += 1

        pos_key = (r, c)
        if pos_key in visited:
            continue
        visited.add(pos_key)

        if 0 <= r < len(game_map) and 0 <= c < len(game_map[0]):
            cell = game_map[r][c]
            cfg = tile_cfg.get(cell, {"points": 0, "damage": 0})
            if cell in VALUABLE_CELLS or cell == "treasure":
                points += cfg.get("points", 0)
                damage += cfg.get("damage", 0)
            elif cell in SPIKE_CELLS:
                damage += cfg.get("damage", 1)

    return points, damage
