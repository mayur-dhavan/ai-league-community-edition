"""
AI League Pathfinder Pro — Premium Score-Maximising Navigation Engine

Strategies:
  swift        — BFS shortest path to treasure (fewest steps)
  get_coins    — Greedily collect c7 coins then reach treasure
  smart_loot   — Score-optimal: visits all loot tiles ranked by value/distance,
                 respects key→door ordering, avoids spikes, life-aware routing
  max_score    — Full optimizer: considers lives budget, damage tolerance,
                 challenge confidence, and net-value calculations to produce
                 the highest possible final score

Scoring model (from game_runner.py):
  finalScore = qaScore + lifeBonusScore + givenTokenBonus + treasureBonus
  where:
    qaScore        = challengePoints + coinPoints
    lifeBonusScore = livesRemaining × livesBonusMultiplier (default 250)
    givenTokenBonus = tokenBonusBase - (totalTokens / challengesVisited) × (1 - reduction)
    treasureBonus  = treasureBonus (default 1000) IF treasure reached

Tile reference (from DEFAULT_TILE_CONFIG):
    c1:  400 pts, 1 dmg  (Violent Violet — guardrail)
    c2:  600 pts, 1 dmg  (Blue Brain — code execution)
    c3:  550 pts, 1 dmg  (Memento — memory)
    c4:  800 pts, 1 dmg  (Dark Prophet — web scraping)
    c5:  250 pts, 1 dmg  (Bonehead — simple trivia)
    c6: 1000 pts, 2 dmg  (Boss Challenge)
    c7:  250 pts, 0 dmg  (Coins — passive, no challenge)
    c8:    0 pts, 1 dmg  (Spikes — passive damage)
    c17: 750 pts, 2 dmg  (Advanced challenge)
    c18: 500 pts, 1 dmg  (Healthcare API)
    c30: 1000 pts, 5 dmg (Red Door — needs c40 key)
    c31: 1000 pts, 5 dmg (Green Door — needs c41 key)
    c32: 1000 pts, 5 dmg (Grey Door — needs c42 key)
    c33: 1000 pts, 5 dmg (Yellow Door — needs c43 key)
    c40:  50 pts, 0 dmg  (Red Key)
    c41:  50 pts, 0 dmg  (Green Key)
    c42:  50 pts, 0 dmg  (Grey Key)
    c43:  50 pts, 0 dmg  (Yellow Key)

Key mechanics:
  - Doors without key: 5 damage, no points
  - Doors with key: challenge for 1000 pts (if assigned)
  - Keys: free pickup (0 dmg), challenge for 50 pts (if assigned)
  - Consumed tiles have no effect on revisit
  - Wall/off-grid = instant death
  - lives <= 0 = game over (lose treasure bonus + remaining life bonus)
"""

import json
from collections import deque
from typing import Dict, List, Optional, Set, Tuple

# ---------------------------------------------------------------------------
# Game constants — mirrored from game_runner.py
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
DOOR_TO_KEY = {"c30": "c40", "c31": "c41", "c32": "c42", "c33": "c43"}
LOOT_CELLS = COIN_CELLS | CHALLENGE_CELLS | KEY_CELLS

# All cells that award points when visited (for max_score calculations)
VALUABLE_CELLS = COIN_CELLS | CHALLENGE_CELLS | KEY_CELLS | DOOR_CELLS


# ---------------------------------------------------------------------------
# Lambda handler
# ---------------------------------------------------------------------------


def lambda_handler(event, context):
    """
    AWS Lambda Pathfinder Pro.

    Accepts game_map (2D grid or JSON string), start_pos, strategy, and optional
    lives/tile_overrides for advanced strategies.

    Strategies: swift | get_coins | smart_loot | max_score

    ## Map Definitions
        - "wall": non-walkable cell
        - "treasure": target cell to reach
        - "normal": walkable cell with no special properties
        - "start": the start cell of your avatar, acts as normal cell
        - "c1"-"c6": challenge tiles (Guardrail, Code, Memory, WebScrape, Simple, Boss)
        - "c7": Coins (passive points, no challenge)
        - "c8": Spikes (passive damage)
        - "c17","c18": advanced challenge tiles
        - "c30"-"c33": Door tiles (need matching key c40-c43)
        - "c40"-"c43": Key tiles (unlock matching door)

    Usage: strategy "max_score" for highest scoring, "smart_loot" for safe collection

    Returns: {"path": [...], "steps": N, "start_position": [...], "strategy_used": "...",
              "estimated_points": N, "estimated_damage": N}
    """
    try:
        # Parse input — support API Gateway, AgentCore, and direct invocation
        if "body" in event:
            body = json.loads(event["body"]) if isinstance(event["body"], str) else event["body"]
        else:
            body = event

        game_map = body.get("game_map", [])
        start_pos = body.get("start_pos", [0, 0])
        strategy = body.get("strategy", "smart_loot")
        lives = body.get("lives", 5)
        tile_overrides = body.get("tile_overrides", {})

        # --- Robust game_map parsing ---
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

        map_object = None
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

        # Execute strategy
        if strategy == "max_score":
            path, est_points, est_damage = max_score_path(
                game_map, rows, cols, start, treasure, lives, tile_cfg
            )
        elif strategy == "smart_loot":
            path, est_points, est_damage = smart_loot_path(
                game_map, rows, cols, start, treasure, lives, tile_cfg
            )
        elif strategy == "get_coins":
            path = get_coins_path(game_map, rows, cols, start, treasure)
            est_points, est_damage = _estimate_path_value(game_map, path, start, tile_cfg)
        else:  # swift
            path = swift_path(game_map, rows, cols, start, treasure)
            est_points, est_damage = _estimate_path_value(game_map, path, start, tile_cfg)

        result = {
            "path": path,
            "steps": len(path),
            "start_position": list(start),
            "strategy_used": strategy,
            "estimated_points": est_points,
            "estimated_damage": est_damage,
        }
        print(f"RESULT: strategy={strategy} steps={len(path)} est_pts={est_points} est_dmg={est_damage}")
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


def _bfs_distance_map(
    game_map, rows: int, cols: int, start: Tuple,
    avoid_cells: Set[str] = None,
    blocked_doors: Set[str] = None,
) -> Dict[Tuple, int]:
    """BFS from start, return distance to all reachable cells."""
    avoid_cells = avoid_cells or set()
    blocked_doors = blocked_doors or set()
    dist = {start: 0}
    queue = deque([start])
    while queue:
        r, c = queue.popleft()
        for dr, dc, _ in DIRECTIONS:
            nr, nc = r + dr, c + dc
            if 0 <= nr < rows and 0 <= nc < cols and (nr, nc) not in dist:
                cell = game_map[nr][nc]
                if cell == "wall" or cell in avoid_cells or cell in blocked_doors:
                    continue
                dist[(nr, nc)] = dist[(r, c)] + 1
                queue.append((nr, nc))
    return dist


# ---------------------------------------------------------------------------
# Strategy: swift
# ---------------------------------------------------------------------------


def swift_path(game_map, rows, cols, start, treasure) -> List[str]:
    """BFS shortest path to treasure — fewest steps possible."""
    return _bfs(game_map, rows, cols, start, treasure) or []


# ---------------------------------------------------------------------------
# Strategy: get_coins
# ---------------------------------------------------------------------------


def get_coins_path(game_map, rows, cols, start, treasure) -> List[str]:
    """Greedily collect nearby c7 coins, then shortest path to treasure."""
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
                if 0 <= nr < rows and 0 <= nc < cols and board[nr][nc] != "wall" and (nr, nc) not in visited:
                    visited.add((nr, nc))
                    queue.append((nr, nc, p + [move]))
        if not targets:
            break
        targets.sort()
        _, path_to, r, c = targets[0]
        full_path.extend(path_to)
        board[r][c] = "normal"

    path_end = _bfs(board, rows, cols, (r, c), treasure)
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
    Visit all valuable tiles in priority order, respect key→door constraints,
    avoid spikes, track life budget. Returns (path, estimated_points, estimated_damage).
    """
    board = [row[:] for row in game_map]
    r, c = start
    full_path = []
    collected_keys: Set[str] = set()  # door IDs that are unlocked
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

            if (cr, cc) != (r, c) and cell in LOOT_CELLS:
                cfg = tile_cfg.get(cell, {"points": 0, "damage": 0})
                pts = cfg["points"]
                dmg = cfg["damage"]
                dist = max(len(p), 1)

                # Skip if we can't afford the damage
                if dmg >= current_lives:
                    pass
                else:
                    # Priority: keys first (0), then value/distance ratio
                    if cell in KEY_CELLS:
                        priority = -1000  # always first
                    else:
                        priority = dist / max(pts, 1) * 250
                    targets.append((priority, dist, cr, cc, list(p), cell))

            # Also target door tiles if we have the key and can afford damage
            if (cr, cc) != (r, c) and cell in DOOR_CELLS and cell in collected_keys:
                cfg = tile_cfg.get(cell, {"points": 1000, "damage": 5})
                pts = cfg["points"]
                dmg = cfg["damage"]
                dist = max(len(p), 1)
                if dmg < current_lives:
                    priority = dist / max(pts, 1) * 250
                    targets.append((priority, dist, cr, cc, list(p), cell))

            for dr, dc, move in DIRECTIONS:
                nr, nc = cr + dr, cc + dc
                if 0 <= nr < rows and 0 <= nc < cols and (nr, nc) not in visited:
                    ncell = board[nr][nc]
                    if ncell == "wall":
                        continue
                    if ncell in SPIKE_CELLS:
                        continue
                    if ncell in locked:
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

    # Final path to treasure (avoiding spikes and locked doors)
    locked = DOOR_CELLS - collected_keys
    path_end = _bfs(board, rows, cols, (r, c), treasure,
                    avoid_cells=SPIKE_CELLS, blocked_doors=locked)
    if path_end is not None:
        full_path.extend(path_end)
    else:
        # Fallback: allow spikes if needed
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
    Maximum-score strategy.

    Considers net value: points_gained - (lives_lost × livesBonusMultiplier).
    Each life is worth 250 points at the end, so a tile is only worth visiting if:
        tile_points > tile_damage × 250

    Additionally:
    - Keys are always collected (free, unlock 1000-pt doors)
    - Coins are always collected (free, 250 pts)
    - Spikes are always avoided (0 pts, cost 250 net)
    - Challenges: visited only if net_value > 0
    - Doors: visited only if key collected AND net_value > 0
    - Orders key before door
    - Considers distance cost: longer paths mean less life bonus doesn't apply here
      since steps don't cost lives, but we still prefer closer targets for efficiency

    Returns (path, estimated_points, estimated_damage).
    """
    LIFE_VALUE = 250  # each remaining life is worth this much in final score

    board = [row[:] for row in game_map]
    r, c = start
    full_path = []
    collected_keys: Set[str] = set()
    total_points = 0
    total_damage = 0
    current_lives = lives

    # Phase 1: Collect all free/positive-net-value loot
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

                # Must be able to survive
                if dmg >= current_lives:
                    pass
                elif cell in KEY_CELLS:
                    # Keys: always collect (free + unlocks 1000-pt door)
                    # Value includes the potential door value
                    door_id = KEY_TO_DOOR[cell]
                    door_cfg = tile_cfg.get(door_id, {"points": 1000, "damage": 5})
                    door_net = door_cfg["points"] - (door_cfg["damage"] * LIFE_VALUE)
                    effective_value = pts + max(door_net, 0)
                    priority = -10000 + dist  # highest priority, tiebreak by distance
                    targets.append((priority, dist, cr, cc, list(p), cell, effective_value))
                elif cell in COIN_CELLS:
                    # Coins: always collect (0 damage, 250 pts)
                    priority = dist  # closer coins first
                    targets.append((priority, dist, cr, cc, list(p), cell, pts))
                elif cell in CHALLENGE_CELLS:
                    # Challenges: only if net value > 0
                    if net_value > 0:
                        # Higher net value and closer = better priority
                        priority = dist * 100.0 / max(net_value, 1)
                        targets.append((priority, dist, cr, cc, list(p), cell, net_value))
                elif cell in DOOR_CELLS and cell in collected_keys:
                    # Unlocked doors: only if net value > 0
                    if net_value > 0:
                        priority = dist * 100.0 / max(net_value, 1)
                        targets.append((priority, dist, cr, cc, list(p), cell, net_value))

            for dr, dc, move in DIRECTIONS:
                nr, nc = cr + dr, cc + dc
                if 0 <= nr < rows and 0 <= nc < cols and (nr, nc) not in visited:
                    ncell = board[nr][nc]
                    if ncell == "wall":
                        continue
                    if ncell in SPIKE_CELLS:
                        continue
                    if ncell in locked:
                        continue
                    visited.add((nr, nc))
                    queue.append((nr, nc, p + [move]))

        if not targets:
            break

        targets.sort(key=lambda x: (x[0], x[1]))
        _, _, r, c, path_to, target_cell, _ = targets[0]
        full_path.extend(path_to)

        cfg = tile_cfg.get(target_cell, {"points": 0, "damage": 0})
        total_points += cfg["points"]
        total_damage += cfg["damage"]
        current_lives -= cfg["damage"]
        board[r][c] = "normal"

        if target_cell in KEY_CELLS:
            collected_keys.add(KEY_TO_DOOR[target_cell])

    # Phase 2: Path to treasure
    locked = DOOR_CELLS - collected_keys
    path_end = _bfs(board, rows, cols, (r, c), treasure,
                    avoid_cells=SPIKE_CELLS, blocked_doors=locked)
    if path_end is not None:
        full_path.extend(path_end)
    else:
        # Try allowing spikes (each spike costs 1 life = 250 pts, still worth it to reach treasure = 1000 pts)
        path_end = _bfs(board, rows, cols, (r, c), treasure, blocked_doors=locked)
        if path_end:
            full_path.extend(path_end)
        else:
            # Last resort: ignore everything, just reach treasure
            path_end = _bfs(board, rows, cols, (r, c), treasure)
            if path_end:
                full_path.extend(path_end)
            else:
                # Unreachable — fallback to swift from original start
                full_path = swift_path(game_map, rows, cols, start, treasure)
                total_points, total_damage = _estimate_path_value(
                    game_map, full_path, start, tile_cfg
                )

    return full_path, total_points, total_damage


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------


def _estimate_path_value(
    game_map, path: List[str], start: Tuple, tile_cfg: Dict
) -> Tuple[int, int]:
    """Walk a path and estimate total points and damage from tiles visited."""
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


def _path_between(
    game_map, rows, cols, start: Tuple, goal: Tuple,
    avoid_cells: Set[str] = None,
    blocked_doors: Set[str] = None,
) -> Optional[List[str]]:
    """Convenience wrapper for _bfs."""
    return _bfs(game_map, rows, cols, start, goal, avoid_cells, blocked_doors)
