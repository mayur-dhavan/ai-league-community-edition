import json
from collections import deque

# Points for every cell type worth visiting
CELL_POINTS = {
    "c7":  250,   # Coin
    "c1":  400,   # Violent Violet (Guardrail)
    "c2":  600,   # Code Challenge
    "c3":  550,   # Memory Trial
    "c4":  800,   # Web Search
    "c5":  250,   # Simple Question
    "c18": 500,   # Healthcare API
}
CHALLENGE_CELLS = {"c1", "c2", "c3", "c4", "c5", "c18"}
LOOT_CELLS      = {"c7"} | CHALLENGE_CELLS   # everything worth going out of your way for
DIRECTIONS = [(-1, 0, "up"), (1, 0, "down"), (0, -1, "left"), (0, 1, "right")]

def lambda_handler(event, context):
    """
    AWS Lambda function for pathfinding using Swift path strategy by default
    Handles both API Gateway format and direct AgentCore Gateway format
    """
    try:
        if 'body' in event:
            body = json.loads(event['body']) if isinstance(event['body'], str) else event['body']
        else:
            body = event

        print(f"DEBUG: Received event: {body}")
        game_map = body.get('game_map', [])
        start_pos = body.get('start_pos', [0, 0])
        strategy = body.get('strategy', 'swift')

        if not game_map:
            return _err(400, 'Missing game_map')

        rows, cols = len(game_map), len(game_map[0])
        treasure = None
        for r in range(rows):
            for c in range(cols):
                if game_map[r][c] == 'treasure':
                    treasure = (r, c)
                    break
            if treasure:
                break

        if not treasure:
            return _err(400, 'No treasure found on map')

        # Added our new custom strategy here
        if strategy == 'smart_loot':
            path = smart_loot_path(game_map, rows, cols, tuple(start_pos), treasure)
        elif strategy == 'get_coins':
            path = get_coins_path(game_map, rows, cols, tuple(start_pos), treasure)
        else:
            path = swift_path(game_map, rows, cols, tuple(start_pos), treasure)

        result = {'path': path, 'steps': len(path), 'start_position': start_pos}
        print(f"RESULT: strategy={strategy} steps={len(path)}")
        return {'statusCode': 200, 'body': json.dumps(result)}

    except Exception as e:
        print(f"ERROR: {e}")
        return _err(500, str(e))

def _err(code, msg):
    return {'statusCode': code, 'body': json.dumps({'error': msg})}

def _bfs(game_map, rows, cols, start, goal):
    """BFS shortest path between two points."""
    queue = deque([(start[0], start[1], [])])
    visited = {(start[0], start[1])}
    while queue:
        r, c, path = queue.popleft()
        if (r, c) == goal:
            return path
        for dr, dc, move in DIRECTIONS:
            nr, nc = r + dr, c + dc
            if 0 <= nr < rows and 0 <= nc < cols and game_map[nr][nc] != 'wall' and (nr, nc) not in visited:
                visited.add((nr, nc))
                queue.append((nr, nc, path + [move]))
    return None

def swift_path(game_map, rows, cols, start, treasure):
    """BFS shortest path to treasure."""
    return _bfs(game_map, rows, cols, start, treasure) or []

def get_coins_path(game_map, rows, cols, start, treasure):
    """Greedily BFS to best coins-per-step c7 cell, then BFS to treasure."""
    board = [row[:] for row in game_map]
    r, c = start
    full_path = []

    for _ in range(50):
        queue = deque([(r, c, [])])
        visited = {(r, c)}
        targets = []
        while queue:
            cr, cc, p = queue.popleft()
            if board[cr][cc] in COLLECTIBLE_COINS and (cr, cc) != (r, c):
                dist = max(len(p), 1)
                targets.append((dist, p, cr, cc))  
            for dr, dc, move in DIRECTIONS:
                nr, nc = cr + dr, cc + dc
                if 0 <= nr < rows and 0 <= nc < cols and board[nr][nc] != 'wall' and (nr, nc) not in visited:
                    visited.add((nr, nc))
                    queue.append((nr, nc, p + [move]))

        if not targets:
            break
        targets.sort()
        _, path_to, r, c = targets[0]
        full_path.extend(path_to)
        board[r][c] = 'normal'

    path_end = _bfs(board, rows, cols, (r, c), treasure)
    if path_end is not None:
        full_path.extend(path_end)
        return full_path
    return swift_path(game_map, rows, cols, start, treasure)

def smart_loot_path(game_map, rows, cols, start, treasure):
    """
    BFS loot collector: visits all c7 coins, challenge cells (c1-c18), and the
    Red Key (c40) before the Red Door (c30). Strictly avoids c8 spike traps.

    Priority formula: (distance × 250 / cell_points) — so an 800-pt c4 is worth
    travelling 3.2× further than a 250-pt coin for the same effective return.
    The Red Key always gets priority 0 so it is collected before anything else
    that might be near the Red Door.
    """
    board = [row[:] for row in game_map]
    r, c = start
    full_path = []
    has_red_key = False

    for _ in range(100):
        queue = deque([(r, c, [])])
        visited = {(r, c)}
        targets = []

        while queue:
            cr, cc, p = queue.popleft()

            cell = board[cr][cc]
            if (cr, cc) != (r, c):
                if cell in LOOT_CELLS:
                    pts = CELL_POINTS.get(cell, 250)
                    dist = max(len(p), 1)
                    # Lower ratio = higher priority (worth more per step)
                    priority = dist * 250 // pts
                    targets.append((priority, dist, cr, cc, p, cell))
                elif cell == 'c40':
                    # Always highest priority — needed before c30
                    targets.append((0, 0, cr, cc, p, 'c40'))

            for dr, dc, move in DIRECTIONS:
                nr, nc = cr + dr, cc + dc
                if 0 <= nr < rows and 0 <= nc < cols:
                    ncell = board[nr][nc]
                    if ncell == 'wall':
                        continue
                    if ncell == 'c8':          # Never step on spikes
                        continue
                    if ncell == 'c30' and not has_red_key:   # Need key first
                        continue
                    if (nr, nc) not in visited:
                        visited.add((nr, nc))
                        queue.append((nr, nc, p + [move]))

        if not targets:
            break

        targets.sort()
        _, _, r, c, path_to, target_type = targets[0]
        full_path.extend(path_to)
        board[r][c] = 'normal'

        if target_type == 'c40':
            has_red_key = True

    # Final BFS to the treasure using the same safety rules
    queue = deque([(r, c, [])])
    visited = {(r, c)}
    path_end = None

    while queue:
        cr, cc, p = queue.popleft()
        if (cr, cc) == treasure:
            path_end = p
            break
        for dr, dc, move in DIRECTIONS:
            nr, nc = cr + dr, cc + dc
            if 0 <= nr < rows and 0 <= nc < cols:
                ncell = board[nr][nc]
                if ncell == 'wall':
                    continue
                if ncell == 'c8':
                    continue
                if ncell == 'c30' and not has_red_key:
                    continue
                if (nr, nc) not in visited:
                    visited.add((nr, nc))
                    queue.append((nr, nc, p + [move]))

    if path_end is not None:
        full_path.extend(path_end)
        return full_path

    # Fallback: direct BFS if all loot paths are blocked
    return swift_path(game_map, rows, cols, start, treasure)