"""AI League Pathfinder - score-optimising navigation engine.

Canonical pathfinder tool. Supersedes pathfinder_pro.py.

Design facts taken from the engine (game_runner.py / score_calculator.py):
  * A wall or off-grid step sets lives to 0 immediately. Never emit one.
  * Tiles are consumed by coordinate; revisiting a consumed tile is a no-op.
    Spikes are therefore a one-time cost and free to walk back over.
  * Treasure ends the run instantly, so it must be the final tile.
  * A door without its key costs 5 damage; a door with its key but a wrong
    answer also costs 5. With 5 starting lives either outcome is fatal.
  * Earned coin/challenge points survive death. Only the life bonus and the
    treasure bonus are forfeited. Risky tiles therefore belong LAST.
  * Movement has no score cost. Path length only matters for the map time
    limit, so the router optimises for safety first and distance second.

Strategies (selectable from the Navigation Prompt, e.g. "use strategy max_points"):
    max_points  Default. Bank deterministic value, take positive-EV challenges,
                order risky tiles last, treasure last. Door only on high confidence.
    max_loot    As max_points but attempts every challenge that has a specialist.
    no_door     max_points with the door forcibly excluded.
    safe        Zero spikes, zero challenges. Straight to treasure - maximises
                the token bonus (no challenges visited returns the full base).
    get_coins   Coins plus treasure only.
    swift       Shortest path to treasure.

Point values default to game-data.md and are overridable per request via
`defaults` and `tile_overrides`.
"""

import hashlib
import heapq
import json
import re
import time
from collections import deque
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

Coord = Tuple[int, int]
Package = Tuple[Coord, Coord]

# Bump on every edit. Combat logs do not expose which Lambda/alias ran or its
# raw response, so this marker is printed to CloudWatch and returned only for
# explicit verbose/direct verification. Keep the scored response path-only lean.
TOOL_VERSION = "2026-10-14.1"

# NOTE: the former CUSTOM_ROUTE replay (a baked route returned for "custom")
# was removed. "Hardcoding navigation paths" is a listed disqualification
# reason, and a replayed route also walks into walls on any board that differs.
# Every route is computed from the game_map the engine sends.


def _remove_target_unit(
    targets: Sequence[Coord],
    unit: Sequence[Coord],
    packages: Sequence[Package] = (),
) -> List[Coord]:
    """Remove one exact-coordinate target/package unit.

    Tile IDs are intentionally ignored: two red keys are distinct coordinates.
    A shared exact key is retained while another active package still uses it.
    """
    remove = set(unit)
    if len(unit) == 2:
        key, door = unit
        present = set(targets)
        if any(
            other_key == key
            and other_door != door
            and other_key in present
            and other_door in present
            for other_key, other_door in packages
        ):
            remove.discard(key)
    return [pos for pos in targets if pos not in remove]

DIRECTIONS: List[Tuple[int, int, str]] = [
    (-1, 0, "up"),
    (1, 0, "down"),
    (0, -1, "left"),
    (0, 1, "right"),
]

WALL = "wall"
TREASURE = "treasure"
START = "start"

COIN_TILES = {"c7"}
SPIKE_TILES = {"c8"}
BASE_CHALLENGE_TILES = frozenset({"c1", "c2", "c3", "c4", "c5", "c6", "c17", "c18"})
CHALLENGE_TILES = set(BASE_CHALLENGE_TILES)  # extended at run time with unseen cN ids
KEY_TILES = {"c40", "c41", "c42", "c43"}
DOOR_TILES = {"c30", "c31", "c32", "c33"}
KEY_TO_DOOR = {"c40": "c30", "c41": "c31", "c42": "c32", "c43": "c33"}
DOOR_TO_KEY = {door: key for key, door in KEY_TO_DOOR.items()}

# Values from game-data.md. Override per request when the in-game guide differs.
DEFAULT_TILE_CONFIG: Dict[str, Dict[str, int]] = {
    "c1": {"points": 400, "damage": 1},   # Violent Violet (guardrail)
    "c2": {"points": 600, "damage": 1},   # Blue Brain (code)
    "c3": {"points": 500, "damage": 1},   # Memento (memory) - not on practice board
    "c4": {"points": 800, "damage": 1},   # Dark Prophet (web)
    "c5": {"points": 250, "damage": 1},   # Bonehead (simple question)
    "c6": {"points": 1000, "damage": 2},  # BOSS - finale only; values unknown, override in strategy
    "c7": {"points": 250, "damage": 0},   # Coins
    "c8": {"points": 0, "damage": 1},     # Spikes
    "c17": {"points": 750, "damage": 2},
    "c18": {"points": 500, "damage": 1},  # Healthcare API (JSON)
    "c30": {"points": 1000, "damage": 5}, # Red door
    "c31": {"points": 1000, "damage": 5}, # Green door
    "c32": {"points": 1000, "damage": 5},
    "c33": {"points": 1000, "damage": 5},
    "c40": {"points": 50, "damage": 0},   # Red key
    "c41": {"points": 50, "damage": 0},   # Green key
    "c42": {"points": 50, "damage": 0},
    "c43": {"points": 50, "damage": 0},
}

DEFAULT_SCORING = {
    "lives": 5,
    "livesBonusMultiplier": 250,
    "tokenBonus": 1000,
    "treasureBonus": 1000,
}

# Probability the agent answers each tile correctly. The red door uses the
# user-selected leaderboard policy: its deterministic key/count pipeline is
# estimated at 0.90, above the one-spike 0.84 break-even. Other doors remain
# opt-in until their contracts are known.
DEFAULT_CONFIDENCE: Dict[str, float] = {
    "c1": 0.90,   # guardrail challenge; say "skip c1" if no guardrail is attached
    "c2": 0.95,   # pycalc tool
    "c3": 0.80,   # memory recall
    "c4": 0.85,   # webfetch tool
    "c5": 0.97,   # terse direct answer
    "c6": 0.65,   # BOSS: composite question (web/calc/map count); "skip c6" to avoid it
    "c17": 0.85,  # list/count questions; the answer is the last item of the list
    "c18": 0.95,  # c18 baked into max_points: attempted by default on ANY board
                  # (no "include c18" needed). The supervisor answers it with the
                  # patient/healthcare (or loan) INTAKE rule. The EV + routing still
                  # skip it when the detour isn't worth it; "skip c18" forces off.
                  # Lower this (e.g. 0.70) to make the planner more selective.
    "c30": 0.98,  # red door via the door tool (reverse)
    "c31": 0.97,  # green door via the door tool (letters to numbers)
    "c32": 0.85,  # grey door: rule given in the question or the first message, else reverse
    "c33": 0.85,  # yellow door: same
    "c40": 0.95,
    "c41": 0.95,
    "c42": 0.95,
    "c43": 0.95,
}

# Free-text overrides from the navigation prompt, e.g.
#   "use strategy max_points skip c4 c31"   -> never attempt those tiles
#   "use strategy max_points include c6"    -> attempt the boss
STRATEGY_SKIP_RE = re.compile(r"(?:skip|avoid|exclude|without|no)\s+((?:c\d+[\s,]*(?:and\s+)?)+)", re.I)
STRATEGY_INCLUDE_RE = re.compile(r"(?:include|with|try|attempt|plus)\s+((?:c\d+[\s,]*(?:and\s+)?)+)", re.I)


def strategy_overrides(raw: Any) -> Dict[str, float]:
    text = str(raw or "")
    out: Dict[str, float] = {}
    for m in STRATEGY_INCLUDE_RE.finditer(text):
        for tile in re.findall(r"c\d+", m.group(1).lower()):
            out[tile] = max(0.95, DEFAULT_CONFIDENCE.get(tile, 0.0))
    for m in STRATEGY_SKIP_RE.finditer(text):
        for tile in re.findall(r"c\d+", m.group(1).lower()):
            out[tile] = 0.0
    return out

# Base door confidence floor. The actual threshold is higher when reaching the
# matching key requires spikes: full candidate and baseline routes are simulated
# and the package is included only when its expected final-score delta is positive.
DEFAULT_DOOR_THRESHOLD = 0.81
DEFAULT_MIN_CONFIDENCE = 0.60

# Risk model. Requiring survival when *every* challenge is answered wrong is far
# too pessimistic: 16 tiles at one damage each always exceeds five lives, so the
# planner would drop every target and collect nothing. Instead budget the
# expected damage with a safety multiplier and keep a life in reserve.
DEFAULT_SAFETY_FACTOR = 2.5
DEFAULT_LIFE_RESERVE = 1.0

# Wall-clock budget for the optional optimisation passes (seconds). The Lambda
# must always answer well inside its timeout, whatever the grid size: when the
# budget runs out the planner keeps the best plan it has instead of refining.
DEFAULT_TIME_BUDGET = 8.0

# Game-clock model, fitted on ten practice runs (timeElapsed in the combat log):
#   elapsed ~= 26 s fixed (setup + the navigation turn) + 0.5 s per step
#              + 4..6 s per graded question (more for long or multi-tool answers).
# A run that hits the clock keeps its banked points and the life bonus but
# forfeits the treasure bonus, so the plan is trimmed to fit the limit.
NAV_OVERHEAD_SECONDS = 26.0
STEP_SECONDS = 0.5
CHALLENGE_SECONDS = 6.0
TILE_SECONDS: Dict[str, float] = {
    "c17": 12.0,  # long list output
    "c6": 14.0,   # several tool calls
    "c4": 9.0,    # web fetch
    "c2": 8.0,    # code execution
    "c40": 3.0, "c41": 3.0, "c42": 3.0, "c43": 3.0,  # keys: one word
}
TIME_SAFETY = 0.90
DEFAULT_TIME_LIMIT = 300.0

# Router penalties, expressed in "equivalent steps". Steps cost nothing in the
# score formula, so a large penalty simply means "detour rather than take this".
PENALTY_UNSELECTED_CHALLENGE = 200.0
PENALTY_DOOR = 5000.0
BLOCKED = float("inf")

NUMBER_WORDS = [
    "ZERO", "ONE", "TWO", "THREE", "FOUR", "FIVE", "SIX", "SEVEN", "EIGHT",
    "NINE", "TEN", "ELEVEN", "TWELVE", "THIRTEEN", "FOURTEEN", "FIFTEEN",
    "SIXTEEN", "SEVENTEEN", "EIGHTEEN", "NINETEEN",
]
TENS_WORDS = [
    "", "", "TWENTY", "THIRTY", "FORTY", "FIFTY", "SIXTY", "SEVENTY",
    "EIGHTY", "NINETY",
]


def number_to_words(value: int) -> str:
    """Render 0-99 as an uppercase English word (99 -> NINETY-NINE)."""
    if value < 0:
        return str(value)
    if value < 20:
        return NUMBER_WORDS[value]
    if value < 100:
        tens, ones = divmod(value, 10)
        return TENS_WORDS[tens] if ones == 0 else f"{TENS_WORDS[tens]}-{NUMBER_WORDS[ones]}"
    return str(value)


# ---------------------------------------------------------------------------
# Input parsing
# ---------------------------------------------------------------------------


def _parse_body(event: Dict[str, Any]) -> Dict[str, Any]:
    """Accept direct, API Gateway, AgentCore Gateway, and action-group shapes."""
    if not isinstance(event, dict):
        return {}

    if "body" in event:
        body = event["body"]
        if isinstance(body, str):
            try:
                return json.loads(body)
            except json.JSONDecodeError:
                return {}
        if isinstance(body, dict):
            return body

    parameters = event.get("parameters")
    if isinstance(parameters, list):
        return {
            str(item.get("name")): item.get("value")
            for item in parameters
            if isinstance(item, dict) and item.get("name")
        }
    if isinstance(parameters, dict):
        return parameters

    # Bedrock action-group requestBody shape.
    content = ((event.get("requestBody") or {}).get("content") or {})
    for media in content.values():
        properties = media.get("properties") if isinstance(media, dict) else None
        if isinstance(properties, list):
            return {
                str(item.get("name")): item.get("value")
                for item in properties
                if isinstance(item, dict) and item.get("name")
            }

    return event


def _loads_loose(value: str) -> Any:
    """Parse JSON that may be wrapped in stray prose from an LLM."""
    text = value.strip()
    lo = next((i for i, ch in enumerate(text) if ch in "[{"), -1)
    hi = next((i for i in range(len(text) - 1, -1, -1) if text[i] in "]}"), -1)
    if lo >= 0 and hi > lo:
        text = text[lo:hi + 1]
    return json.loads(text)


def _extract_grid(raw: Any) -> Tuple[List[List[str]], Dict[str, Any]]:
    """Return (grid, map_object).

    ``game_map`` is the 2D array of tile ids (a JSON array of rows, each row an
    array of strings such as "normal", "wall", "c7"). A JSON string encoding
    that array is tolerated for callers that cannot send a real array."""
    map_object: Dict[str, Any] = {}
    if isinstance(raw, str):
        raw = _loads_loose(raw)
    if isinstance(raw, dict):
        map_object = raw
        raw = raw.get("grid") or raw.get("game_map") or raw.get("map") or []
        if isinstance(raw, str):
            raw = _loads_loose(raw)
    if not isinstance(raw, list) or not raw:
        raise ValueError("game_map must be a non-empty 2D array")

    width = max(len(row) for row in raw)
    if any(len(row) != width for row in raw):
        # A short row means the caller dropped a cell. The true layout cannot
        # be recovered; padding with walls keeps the planner out of the unknown.
        print(f"WARNING: jagged map rows {[len(row) for row in raw]}, padding with wall")
    grid = [[str(cell) for cell in row] + [WALL] * (width - len(row)) for row in raw]
    return grid, map_object


def normalize_strategy(raw: Any) -> str:
    """Map free-text navigation prompts onto a known strategy name."""
    text = str(raw or "").lower()
    for name in ("no_door", "no door", "nodoor"):
        if name in text:
            return "no_door"
    if "max_loot" in text or "maxloot" in text or "loot" in text or "everything" in text \
            or "all challenges" in text or "all the challenges" in text:
        return "max_loot"
    if "max_points" in text or "maxpoints" in text or "max_score" in text or "point" in text:
        return "max_points"
    if "safe" in text:
        return "safe"
    if "coin" in text:
        return "get_coins"
    if "swift" in text or "fast" in text or "quick" in text or "short" in text \
            or "direct" in text or "optimize for time" in text or "optimise for time" in text:
        return "swift"
    return "max_points"


MAX_CHALLENGES_RE = re.compile(r"(?:max|at most|up to|no more than|limit(?:ed)? to)\s+(\d+)\s+challenge", re.I)


def strategy_max_challenges(raw: Any) -> Optional[int]:
    """'use strategy max_points, at most 6 challenges' -> 6."""
    m = MAX_CHALLENGES_RE.search(str(raw or ""))
    return int(m.group(1)) if m else None


# The map's clock is not in the navigation prompt the engine sends, so the
# player types it: "use strategy max_points time 65", "time limit 1:05",
# "65 seconds", "2 minutes". Anything under 10 s is ignored as noise.
TIME_LIMIT_RE = re.compile(
    r"(?:time(?:\s*limit)?|clock|timer|limit)\s*(?:is|of|=|:)?\s*(\d+(?:[.:]\d+)?)\s*"
    r"(m|min|mins|minute|minutes|s|sec|secs|second|seconds)?\b",
    re.I,
)
DURATION_RE = re.compile(
    r"\b(\d+(?:[.:]\d+)?)\s*(m|min|mins|minute|minutes|s|sec|secs|second|seconds)\b", re.I
)


def strategy_time_limit(raw: Any) -> Optional[float]:
    """Seconds on the map clock as typed in the navigation prompt, or None."""
    text = str(raw or "")
    m = TIME_LIMIT_RE.search(text) or DURATION_RE.search(text)
    if not m:
        return None
    number, unit = m.group(1), (m.group(2) or "").lower()
    if ":" in number:  # "1:05" -> 65 s
        minutes, seconds = number.split(":", 1)
        value = int(minutes) * 60 + int(seconds or 0)
    else:
        value = float(number)
        if unit.startswith("m"):
            value *= 60.0
        elif not unit and value <= 10:  # "time 2" means minutes
            value *= 60.0
    return float(value) if value >= 10 else None


def _find(grid: Sequence[Sequence[str]], predicate) -> List[Coord]:
    return [
        (r, c)
        for r, row in enumerate(grid)
        for c, cell in enumerate(row)
        if predicate(cell)
    ]


def parse_position(value: Any) -> Optional[Coord]:
    """Parse a position from any shape the game or an LLM might send.

    Accepts [row, col], {"row": r, "col": c}, "[5,4]", and column-letter labels
    such as "E6" (column E = index 4, row 6 = index 5). Label support matters:
    the navigation prompt states the start as a label, and a run where the
    sub-agent passed the wrong numeric pair shifted the whole path by one row
    and walked into a red door.
    """
    if value is None:
        return None

    if isinstance(value, dict):
        if "row" in value and "col" in value:
            try:
                return (int(value["row"]), int(value["col"]))
            except (TypeError, ValueError):
                return None
        return None

    if isinstance(value, (list, tuple)):
        if len(value) == 1:
            return parse_position(value[0])
        if len(value) >= 2:
            try:
                return (int(value[0]), int(value[1]))
            except (TypeError, ValueError):
                # Could be ["E", "6"]
                text = f"{value[0]}{value[1]}"
                return parse_position(text)
        return None

    if isinstance(value, str):
        text = value.strip()
        try:
            parsed = _loads_loose(text)
            if not isinstance(parsed, str):
                return parse_position(parsed)
        except (json.JSONDecodeError, ValueError):
            pass

        label = re.match(r"^\s*([A-Za-z])\s*(\d+)\s*$", text)
        if label:
            col = ord(label.group(1).upper()) - ord("A")
            row = int(label.group(2)) - 1
            if row >= 0:
                return (row, col)

        numbers = re.findall(r"\d+", text)
        if len(numbers) >= 2:
            return (int(numbers[0]), int(numbers[1]))

    return None


def _resolve_start(
    grid: Sequence[Sequence[str]], body: Dict[str, Any], map_object: Dict[str, Any]
) -> Coord:
    """Determine the start cell, trusting the grid over anything an LLM sends."""
    rows, cols = len(grid), len(grid[0])

    def valid(pos: Optional[Coord]) -> bool:
        return (
            pos is not None
            and 0 <= pos[0] < rows
            and 0 <= pos[1] < cols
            and grid[pos[0]][pos[1]] != WALL
        )

    # The grid itself is authoritative when it marks the start tile.
    start_tiles = _find(grid, lambda cell: cell == START)
    if start_tiles:
        return start_tiles[0]

    # `start_label` comes first on purpose. It is the position copied verbatim
    # from the prompt, e.g. "E6", so the model does no arithmetic: a model that
    # converts the label to numbers itself can be off by one row, and a route
    # that starts one row off walks into a wall. A verbatim label cannot be.
    for candidate in (
        body.get("start_label"),
        body.get("startLabel"),
        map_object.get("playerStart"),
        body.get("playerStart"),
        body.get("start_pos"),
        body.get("start"),
        body.get("position"),
    ):
        pos = parse_position(candidate)
        if valid(pos):
            return pos

    return (0, 0)


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------


def _tile_cost(
    cell: str,
    pos: Coord,
    *,
    consumed: Set[Coord],
    unlocked: Set[str],
    tile_cfg: Dict[str, Dict[str, int]],
    life_value: int,
    selected: Set[Coord],
    goal: Coord,
    allow_treasure: bool,
    allow_locked_doors: bool = False,
) -> float:
    """Cost of stepping onto `pos`. BLOCKED means never route through it."""
    if cell == WALL:
        return BLOCKED
    if cell == TREASURE:
        return 1.0 if (allow_treasure and pos == goal) else BLOCKED
    if pos in consumed:
        return 1.0  # already consumed: inert and free
    if cell in COIN_TILES:
        return 0.1  # free points, prefer routing over them
    if cell in KEY_TILES:
        # Untargeted keys still invoke the agent and can accidentally unlock a
        # package that repair deliberately removed. Treat them like optional
        # challenges: take a detour when one exists, cross only if unavoidable.
        return 0.5 if pos == goal or pos in selected else PENALTY_UNSELECTED_CHALLENGE
    if cell in SPIKE_TILES:
        return 1.0 + tile_cfg.get(cell, {}).get("damage", 1) * life_value
    if cell in DOOR_TILES:
        # A locked door costs 5 damage, which is fatal at 5 lives. It must be
        # BLOCKED, never merely expensive. A real run routed through c30 to
        # reach a coin behind it, paid the penalty, and finished with score 0.
        if cell not in unlocked:
            return 1.0 if allow_locked_doors else BLOCKED
        if pos == goal:
            return 1.0
        return PENALTY_DOOR
    if cell in CHALLENGE_TILES:
        if pos == goal or pos in selected:
            return 1.0
        return PENALTY_UNSELECTED_CHALLENGE
    return 1.0


def _route(
    grid: Sequence[Sequence[str]],
    start: Coord,
    goal: Coord,
    *,
    consumed: Set[Coord],
    unlocked: Set[str],
    tile_cfg: Dict[str, Dict[str, int]],
    life_value: int,
    selected: Set[Coord],
    allow_treasure: bool = False,
    allow_locked_doors: bool = False,
) -> Optional[List[Coord]]:
    """Least-cost route from start to goal, excluding the start cell.

    Uses uniform-cost search so that damage tiles are avoided whenever any
    detour exists, since extra steps are free in the score formula.
    """
    if start == goal:
        return []
    rows, cols = len(grid), len(grid[0])
    best: Dict[Coord, float] = {start: 0.0}
    heap: List[Tuple[float, int, Coord, List[Coord]]] = [(0.0, 0, start, [])]
    counter = 1

    while heap:
        cost, _, current, path = heapq.heappop(heap)
        if current == goal:
            return path
        if cost > best.get(current, BLOCKED):
            continue
        for dr, dc, _name in DIRECTIONS:
            nxt = (current[0] + dr, current[1] + dc)
            if not (0 <= nxt[0] < rows and 0 <= nxt[1] < cols):
                continue
            step = _tile_cost(
                grid[nxt[0]][nxt[1]],
                nxt,
                consumed=consumed,
                unlocked=unlocked,
                tile_cfg=tile_cfg,
                life_value=life_value,
                selected=selected,
                goal=goal,
                allow_treasure=allow_treasure,
                allow_locked_doors=allow_locked_doors,
            )
            if step == BLOCKED:
                continue
            new_cost = cost + step
            if new_cost < best.get(nxt, BLOCKED):
                best[nxt] = new_cost
                heapq.heappush(heap, (new_cost, counter, nxt, path + [nxt]))
                counter += 1
    return None


def _to_directions(path: Sequence[Coord]) -> List[str]:
    """Convert a coordinate path into direction words.

    The stock tool shipped with the real game returns direction words, and its
    sub-agent prompt returns "only the path array", so directions are the
    proven contract. Absolute coordinates are offered as a fallback for
    engines that accept them.
    """
    lookup = {(-1, 0): "up", (1, 0): "down", (0, -1): "left", (0, 1): "right"}
    moves: List[str] = []
    for (r1, c1), (r2, c2) in zip(path, path[1:]):
        move = lookup.get((r2 - r1, c2 - c1))
        if move is None:
            raise ValueError(f"non-adjacent step from {(r1, c1)} to {(r2, c2)}")
        moves.append(move)
    return moves


_PLAN_DEADLINE = [float("inf")]


def _bfs_distances(grid: Sequence[Sequence[str]], src: Coord) -> Dict[Coord, int]:
    """Step counts from `src` to every cell reachable without crossing a wall."""
    rows, cols = len(grid), len(grid[0])
    dist = {src: 0}
    queue = deque([src])
    while queue:
        r, c = queue.popleft()
        d = dist[(r, c)] + 1
        for dr, dc, _ in DIRECTIONS:
            nxt = (r + dr, c + dc)
            if nxt in dist or not (0 <= nxt[0] < rows and 0 <= nxt[1] < cols):
                continue
            if grid[nxt[0]][nxt[1]] == WALL:
                continue
            dist[nxt] = d
            queue.append(nxt)
    return dist


def _walk_distance(
    grid: Sequence[Sequence[str]], start: Coord, goal: Coord
) -> Optional[int]:
    """Plain BFS step count ignoring tile effects, used for ordering only."""
    if start == goal:
        return 0
    rows, cols = len(grid), len(grid[0])
    queue = deque([(start, 0)])
    seen = {start}
    while queue:
        (r, c), dist = queue.popleft()
        for dr, dc, _ in DIRECTIONS:
            nxt = (r + dr, c + dc)
            if nxt in seen or not (0 <= nxt[0] < rows and 0 <= nxt[1] < cols):
                continue
            if grid[nxt[0]][nxt[1]] == WALL:
                continue
            if nxt == goal:
                return dist + 1
            seen.add(nxt)
            queue.append((nxt, dist + 1))
    return None


def _nearest_order(
    grid: Sequence[Sequence[str]], start: Coord, targets: List[Coord]
) -> List[Coord]:
    """Greedy nearest-neighbour ordering, kept for the coin-only strategies.

    Movement itself is not scored, but every emitted direction is an output
    token and the token bonus is base - (tokens / challengesVisited). Use
    `_tour_order` for anything that lands in the scored navigation answer.
    """
    remaining = list(targets)
    ordered: List[Coord] = []
    current = start
    while remaining:
        dist = _bfs_distances(grid, current)
        chosen = min(remaining, key=lambda t: (dist.get(t, 10**6), t))
        ordered.append(chosen)
        remaining.remove(chosen)
        current = chosen
    return ordered


def _open_region(grid: Sequence[Sequence[str]], start: Coord) -> Set[Coord]:
    """Cells reachable from `start` without crossing a locked door.

    Tiles sealed behind a door must not distort the tour: `_assemble` defers
    them until the matching key is collected, so ordering them as if they were
    freely reachable produces a tour whose measured length is a lie.
    """
    rows, cols = len(grid), len(grid[0])
    seen = {start}
    queue = deque([start])
    while queue:
        r, c = queue.popleft()
        for dr, dc, _name in DIRECTIONS:
            nxt = (r + dr, c + dc)
            if nxt in seen or not (0 <= nxt[0] < rows and 0 <= nxt[1] < cols):
                continue
            cell = grid[nxt[0]][nxt[1]]
            if cell == WALL or cell in DOOR_TILES:
                continue
            seen.add(nxt)
            queue.append(nxt)
    return seen


def _tour_order(
    grid: Sequence[Sequence[str]],
    start: Coord,
    targets: Sequence[Coord],
    risk: Optional[Dict[Coord, float]] = None,
    finish: Optional[Coord] = None,
) -> List[Coord]:
    """Distance-minimising visit order: multi-start nearest neighbour + 2-opt.

    Replaces confidence-tier bucketing, which visited every tile of one
    confidence band before moving to the next and produced tours far longer
    than necessary. Every wasted move is output the model must echo.

    Confidence is retained only as a tie-break: among tours of equal length the
    one that visits fragile tiles later wins, preserving the original intent of
    banking safe points before risking a life. The key/door package is appended
    by the caller after this ordering, so the riskiest tile stays last either way.

    `finish` charges the closing leg from the last target to wherever the route
    continues, which is the key when a door package follows and the treasure
    otherwise. Without it the tour optimises to a dead end: it ended in the
    top-left corner and then paid 20 moves to walk back to a key in the bottom row.
    """
    if len(targets) <= 1:
        return list(targets)

    weights = dict(risk or {})
    nodes = [start] + list(targets)
    if finish is not None and finish not in nodes:
        nodes.append(finish)
    dist: Dict[Coord, Dict[Coord, int]] = {}
    for a in nodes:
        from_a = _bfs_distances(grid, a)
        dist[a] = {b: (0 if a == b else from_a.get(b, 10**6)) for b in nodes}

    def length(order: Sequence[Coord]) -> int:
        total = 0
        current = start
        for node in order:
            total += dist[current][node]
            current = node
        if finish is not None:
            total += dist[current][finish]
        return total

    def lateness(order: Sequence[Coord]) -> float:
        # Lower is better, so a fragile tile placed early costs more.
        span = len(order)
        return sum(weights.get(pos, 0.0) * (span - index)
                   for index, pos in enumerate(order))

    def cost(order: Sequence[Coord]) -> Tuple[int, float]:
        return (length(order), lateness(order))

    best: Optional[List[Coord]] = None
    best_cost: Optional[Tuple[int, float]] = None

    # Multi-start from the few nearest targets only; a full multi-start is
    # cubic in the target count and the precedence-aware tour pass refines
    # the order again later.
    seeds = sorted(targets, key=lambda t: (dist[start][t], t))[:4]
    for seed in seeds:
        if time.time() > _PLAN_DEADLINE[0]:
            break
        remaining = set(targets)
        order = [seed]
        remaining.discard(seed)
        current = seed
        while remaining:
            nxt = min(
                remaining,
                key=lambda t: (dist[current][t], weights.get(t, 0.0), t),
            )
            order.append(nxt)
            remaining.discard(nxt)
            current = nxt

        improved = True
        while improved and time.time() < _PLAN_DEADLINE[0]:
            improved = False
            current_cost = cost(order)
            for i in range(len(order) - 1):
                if time.time() > _PLAN_DEADLINE[0]:
                    break
                for j in range(i + 2, len(order) + 1):
                    candidate = order[:i] + order[i:j][::-1] + order[j:]
                    candidate_cost = cost(candidate)
                    if candidate_cost < current_cost:
                        order = candidate
                        current_cost = candidate_cost
                        improved = True

        final_cost = cost(order)
        if best_cost is None or final_cost < best_cost:
            best, best_cost = list(order), final_cost

    return best or list(targets)


# ---------------------------------------------------------------------------
# Simulation - mirrors game_runner.py tile handling
# ---------------------------------------------------------------------------


def simulate_path(
    grid: Sequence[Sequence[str]],
    path: Sequence[Coord],
    *,
    lives: int,
    tile_cfg: Dict[str, Dict[str, int]],
    life_value: int,
    treasure_bonus: int,
    confidence: Dict[str, float],
    mode: str = "expected",
) -> Dict[str, Any]:
    """Replay a path with explicit success/failure branches.

    A door failure is terminal at five damage. Averaging its damage first and
    then awarding treasure to the resulting fractional life total materially
    overvalues the door, so expected mode keeps complete outcome branches until
    the final score is calculated. Worst and best mode force every branch to
    fail or succeed respectively.

    Branches are aggregated by (lives remaining, status): the score is linear
    in banked points, so each bucket keeps its total probability and its
    probability-weighted points. Without this the branch count doubles at
    every graded tile and a twenty-challenge route takes seconds to price.
    """
    rows, cols = len(grid), len(grid[0])
    consumed: Set[Coord] = set()
    unlocked: Set[str] = set()
    # bucket (remaining lives, status) -> [probability, probability-weighted points]
    states: Dict[Tuple[float, str], List[float]] = {(float(lives), "active"): [1.0, 0.0]}
    challenges = 0
    illegal_step: Optional[List[int]] = None
    damage_certain = 0.0
    damage_expected = 0.0

    def active_probability() -> float:
        return sum(pw[0] for (_hp, status), pw in states.items() if status == "active")

    def _add(bucket: Dict[Tuple[float, str], List[float]], key: Tuple[float, str], prob: float, wpts: float) -> None:
        if prob <= 0.0:
            return
        slot = bucket.get(key)
        if slot is None:
            bucket[key] = [prob, wpts]
        else:
            slot[0] += prob
            slot[1] += wpts

    def hurt(damage: float) -> None:
        """Every active branch takes `damage` with certainty."""
        nonlocal states
        updated: Dict[Tuple[float, str], List[float]] = {}
        for (remaining, status), (prob, wpts) in states.items():
            if status != "active":
                _add(updated, (remaining, status), prob, wpts)
                continue
            after = max(0.0, remaining - damage)
            _add(updated, (after, "dead" if after <= 0 else "active"), prob, wpts)
        states = updated

    def grade(cell: str) -> None:
        nonlocal states, damage_expected
        cfg = tile_cfg.get(cell, {"points": 0, "damage": 0})
        success = float(confidence.get(cell, 0.5))
        if mode == "worst":
            success = 0.0
        elif mode == "best":
            success = 1.0
        success = min(1.0, max(0.0, success))
        failure = 1.0 - success

        updated: Dict[Tuple[float, str], List[float]] = {}
        for (remaining, status), (prob, wpts) in states.items():
            if status != "active":
                _add(updated, (remaining, status), prob, wpts)
                continue
            if success:
                _add(updated, (remaining, "active"), prob * success,
                     success * (wpts + prob * cfg["points"]))
            if failure:
                after = max(0.0, remaining - cfg["damage"])
                _add(updated, (after, "dead" if after <= 0 else "active"),
                     prob * failure, failure * wpts)
                damage_expected += prob * failure * cfg["damage"]
        states = updated

    for pos in path:
        if active_probability() <= 0:
            break
        r, c = pos
        if not (0 <= r < rows and 0 <= c < cols) or grid[r][c] == WALL:
            illegal_step = [r, c]
            updated: Dict[Tuple[float, str], List[float]] = {}
            for (remaining, status), (prob, wpts) in states.items():
                if status == "active":
                    _add(updated, (0.0, "dead"), prob, wpts)
                else:
                    _add(updated, (remaining, status), prob, wpts)
            states = updated
            break

        if pos in consumed:
            continue
        cell = grid[r][c]

        if cell == TREASURE:
            updated = {}
            for (remaining, status), (prob, wpts) in states.items():
                _add(updated, (remaining, "treasure" if status == "active" else status), prob, wpts)
            states = updated
            consumed.add(pos)
            break

        if cell in COIN_TILES:
            reward = tile_cfg.get(cell, {}).get("points", 0)
            for (_remaining, status), pw in states.items():
                if status == "active":
                    pw[1] += pw[0] * reward
            consumed.add(pos)
            continue

        if cell in SPIKE_TILES:
            hit = tile_cfg.get(cell, {}).get("damage", 1)
            damage_certain += active_probability() * hit
            hurt(hit)
            consumed.add(pos)
            continue

        if cell in DOOR_TILES:
            consumed.add(pos)
            cfg = tile_cfg.get(cell, {"points": 0, "damage": 5})
            if cell not in unlocked:
                damage_certain += active_probability() * cfg["damage"]
                hurt(cfg["damage"])
            else:
                challenges += 1
                grade(cell)
            continue

        if cell in KEY_TILES:
            # The live contract says the key unlock happens before key grading.
            unlocked.add(KEY_TO_DOOR[cell])
            consumed.add(pos)
            challenges += 1
            grade(cell)
            continue

        if cell in CHALLENGE_TILES:
            consumed.add(pos)
            challenges += 1
            grade(cell)

    expected_points = sum(pw[1] for pw in states.values())
    expected_lives = sum(pw[0] * max(0.0, hp) for (hp, _status), pw in states.items())
    treasure_probability = sum(pw[0] for (_hp, status), pw in states.items() if status == "treasure")
    death_probability = sum(pw[0] for (_hp, status), pw in states.items() if status == "dead")
    expected_score = sum(
        wpts
        + prob * max(0.0, remaining) * life_value
        + prob * (treasure_bonus if status == "treasure" else 0)
        for (remaining, status), (prob, wpts) in states.items()
    )
    return {
        "points": round(expected_points, 2),
        "lives_remaining": round(expected_lives, 2),
        "challenges": challenges,
        "reached_treasure": treasure_probability > 0,
        "treasure_probability": round(treasure_probability, 6),
        "died": death_probability >= 1.0 - 1e-9,
        "death_probability": round(death_probability, 6),
        "illegal_step": illegal_step,
        "damage_certain": round(damage_certain, 2),
        "damage_expected": round(damage_expected, 2),
        "estimated_score_excluding_token_bonus": round(expected_score, 2),
    }


# ---------------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------------


def _expected_value(cell: str, tile_cfg: Dict, confidence: Dict, life_value: int) -> float:
    cfg = tile_cfg.get(cell, {"points": 0, "damage": 0})
    prob = float(confidence.get(cell, 0.5))
    return prob * cfg["points"] - (1.0 - prob) * cfg["damage"] * life_value


def _select_targets(
    grid: Sequence[Sequence[str]],
    start: Coord,
    treasure: Coord,
    strategy: str,
    *,
    tile_cfg: Dict[str, Dict[str, int]],
    confidence: Dict[str, float],
    life_value: int,
    treasure_bonus: int,
    lives: int,
    max_challenges: int,
    door_threshold: float,
    min_confidence: float,
) -> Tuple[List[Coord], List[str], List[Tuple[Coord, Coord]]]:
    """Choose ordered targets and return exact-coordinate key-door packages.

    A key is not valued in isolation. Each candidate is assembled as the full
    current route -> matching key -> door -> treasure and compared with the
    same route going directly to treasure. This captures one-time spike costs,
    terminal door failure, life bonus, treasure, and any already-banked loot.
    Package coordinates remain paired so later repair cannot remove every key
    of the same colour or silently turn a profitable package into an orphan.
    """
    notes: List[str] = []
    packages: List[Tuple[Coord, Coord]] = []
    if strategy == "swift":
        return [], ["swift: treasure only"], []

    coins = _find(grid, lambda cell: cell in COIN_TILES)
    if strategy == "safe":
        return [], ["safe: no coins, no challenges, maximum token bonus"], []
    if strategy == "get_coins":
        return _nearest_order(grid, start, coins), ["get_coins: coins plus treasure"], []

    keys = _find(grid, lambda cell: cell in KEY_TILES)
    doors = _find(grid, lambda cell: cell in DOOR_TILES)
    challenge_tiles = _find(grid, lambda cell: cell in CHALLENGE_TILES)

    # Ordinary challenges: positive expected value, safest first. Door and key
    # invocations are added later as an indivisible two-target package.
    floor = 0.0 if strategy == "max_loot" else min_confidence
    scored: List[Tuple[float, float, Coord]] = []
    for pos in challenge_tiles:
        cell = grid[pos[0]][pos[1]]
        prob = float(confidence.get(cell, 0.5))
        value = _expected_value(cell, tile_cfg, confidence, life_value)
        if prob < floor:
            notes.append(f"{cell} skipped: confidence {prob:.2f} below {floor:.2f}")
            continue
        if value <= 0 and strategy != "max_loot":
            notes.append(f"{cell} skipped: expected value {value:.0f}")
            continue
        scored.append((prob, value, pos))

    scored.sort(key=lambda item: (-item[0], -item[1]))
    # Reserve two invocations (key + door) per door that has a matching key on
    # the map, so the challenge budget cannot crowd out a package that may be
    # the only way to the treasure. Unused reservations are handed back below.
    door_cells_present = {grid[pos[0]][pos[1]] for pos in doors}
    reservable = sum(
        1 for key in keys if KEY_TO_DOOR[grid[key[0]][key[1]]] in door_cells_present
    ) if strategy != "no_door" else 0
    ordinary_budget = max(1, max_challenges - 2 * reservable)
    overflow: List[Tuple[float, float, Coord]] = []
    if len(scored) > ordinary_budget:
        notes.append(
            f"challenge budget {max_challenges} ({ordinary_budget} ordinary), "
            f"{len(scored) - ordinary_budget} deferred"
        )
        overflow = scored[ordinary_budget:]
        scored = scored[:ordinary_budget]

    # Coins and challenges share one distance-optimised tour. Confidence is a
    # tie-break inside _tour_order rather than a hard bucketing, because tiering
    # forced a full traverse of the board per confidence band. Tiles sealed
    # behind a locked door are held back: _assemble retries them once the
    # matching key is collected, so including them here would both distort the
    # tour and reorder the door package.
    open_region = _open_region(grid, start)
    risk = {pos: 1.0 - prob for prob, _value, pos in scored}
    candidates = list(coins) + [pos for _prob, _value, pos in scored]
    reachable_now = [pos for pos in candidates if pos in open_region]
    sealed = [pos for pos in candidates if pos not in open_region]

    # After the tour the route continues into the key/door package when one is
    # viable, otherwise straight to treasure. Charging that closing leg stops the
    # tour from finishing in a far corner and paying for the walk back.
    door_cells = {grid[pos[0]][pos[1]] for pos in doors}
    viable_keys = [
        key for key in keys
        if strategy != "no_door"
        and KEY_TO_DOOR[grid[key[0]][key[1]]] in door_cells
        and float(confidence.get(KEY_TO_DOOR[grid[key[0]][key[1]]], 0.0)) >= door_threshold
    ]
    finish = min(
        viable_keys, key=lambda k: _walk_distance(grid, start, k) or 10**6
    ) if viable_keys else treasure

    ordered = _tour_order(grid, start, reachable_now, risk, finish)
    if sealed:
        notes.append(
            f"{len(sealed)} tile(s) sealed behind a door, ordered after the key"
        )
        ordered += sealed

    if strategy == "no_door":
        notes.append("no_door: doors excluded by strategy")
        if keys:
            notes.append(f"{len(keys)} key(s) not targeted: doors disabled")
        return ordered, notes, []

    def evaluate(targets: Sequence[Coord]) -> Tuple[Optional[List[Coord]], Optional[Dict[str, Any]]]:
        candidate_path, _visited, _unreachable = _assemble(
            grid, start, targets, treasure, tile_cfg, life_value, confidence
        )
        if candidate_path is None:
            return None, None
        result = simulate_path(
            grid,
            candidate_path,
            lives=lives,
            tile_cfg=tile_cfg,
            life_value=life_value,
            treasure_bonus=treasure_bonus,
            confidence=confidence,
            mode="expected",
        )
        return candidate_path, result

    remaining_doors = list(doors)
    while remaining_doors:
        baseline_path, baseline = evaluate(ordered)
        baseline_score = (
            baseline["estimated_score_excluding_token_bonus"] if baseline else 0.0
        )
        best: Optional[Tuple[float, Coord, Coord, List[Coord], Dict[str, Any]]] = None

        for door in list(remaining_doors):
            door_cell = grid[door[0]][door[1]]
            probability = float(confidence.get(door_cell, 0.0))
            matching = [
                key for key in keys if grid[key[0]][key[1]] == DOOR_TO_KEY[door_cell]
            ]
            if not matching:
                notes.append(f"door {door_cell} skipped: no matching key on map")
                remaining_doors.remove(door)
                continue
            if probability < door_threshold:
                notes.append(
                    f"door {door_cell} skipped: confidence {probability:.2f} "
                    f"below base floor {door_threshold:.2f}"
                )
                remaining_doors.remove(door)
                continue

            for key in matching:
                package_targets = list(ordered)
                if key not in package_targets:
                    package_targets.append(key)
                package_targets.append(door)
                candidate_path, candidate = evaluate(package_targets)
                if candidate_path is None or candidate is None:
                    continue
                if candidate["illegal_step"] is not None or not candidate["reached_treasure"]:
                    continue
                if candidate["challenges"] > max_challenges:
                    continue
                delta = (
                    candidate["estimated_score_excluding_token_bonus"] - baseline_score
                )
                if best is None or delta > best[0]:
                    best = (delta, key, door, candidate_path, candidate)

        if best is None or best[0] <= 0:
            # A candidate can clear the base 0.81 floor yet still be a bad bet
            # once its required spikes and route are included.
            for door in remaining_doors:
                cell = grid[door[0]][door[1]]
                notes.append(
                    f"door {cell} skipped: full key-door package has non-positive expected score"
                )
            break

        delta, key, door, candidate_path, candidate = best
        if key not in ordered:
            ordered.append(key)
        ordered.append(door)
        packages.append((key, door))
        remaining_doors.remove(door)

        baseline_spikes = {
            pos for pos in (baseline_path or []) if grid[pos[0]][pos[1]] in SPIKE_TILES
        }
        package_spikes = {
            pos for pos in candidate_path if grid[pos[0]][pos[1]] in SPIKE_TILES
        }
        added_spikes = len(package_spikes - baseline_spikes)
        door_cell = grid[door[0]][door[1]]
        notes.append(
            f"door {door_cell} package included: key {grid[key[0]][key[1]]} first, "
            f"{added_spikes} incremental spike(s), expected delta {delta:.0f}, "
            f"treasure probability {candidate['treasure_probability']:.2f}"
        )

    selected_doors = {grid[pos[0]][pos[1]] for pos in ordered if grid[pos[0]][pos[1]] in DOOR_TILES}
    untargeted_keys = [
        key for key in keys if KEY_TO_DOOR[grid[key[0]][key[1]]] not in selected_doors
    ]
    if untargeted_keys:
        notes.append(
            f"{len(untargeted_keys)} key(s) not targeted: matching package not profitable"
        )

    # Hand back reserved invocations that no package used, to the next best
    # ordinary challenges, keeping the risk ordering (safest first).
    if overflow:
        used = sum(
            1 for pos in ordered
            if grid[pos[0]][pos[1]] in CHALLENGE_TILES
            or grid[pos[0]][pos[1]] in KEY_TILES
            or grid[pos[0]][pos[1]] in DOOR_TILES
        )
        room = max_challenges - used
        if room > 0:
            extra = [pos for _prob, _value, pos in overflow[:room]]
            # Insert before the first package element so packages stay last.
            first_pkg = next(
                (i for i, pos in enumerate(ordered)
                 if grid[pos[0]][pos[1]] in KEY_TILES or grid[pos[0]][pos[1]] in DOOR_TILES),
                len(ordered),
            )
            ordered = ordered[:first_pkg] + extra + ordered[first_pkg:]
            notes.append(f"{len(extra)} deferred challenge(s) restored into the plan")
    return ordered, notes, packages


def plan(body: Dict[str, Any]) -> Dict[str, Any]:
    """Build a validated, runner-compatible path plan."""
    started_at = time.time()
    deadline = started_at + float(body.get("time_budget") or DEFAULT_TIME_BUDGET)
    # The optimisation passes share 75% of the budget; the rest is reserved for
    # the time-fit pass so a route is always trimmed to the map clock.
    main_deadline = started_at + 0.75 * (deadline - started_at)
    # The tour heuristics inside _select_targets get a little less than the
    # whole budget so the repair and tour passes still run on big grids.
    _PLAN_DEADLINE[0] = started_at + 0.6 * (deadline - started_at)
    grid, map_object = _extract_grid(body.get("game_map") or body.get("grid"))
    rows, cols = len(grid), len(grid[0])

    # Tile ids this planner has never seen (a new finale challenge type) are
    # treated as challenges of unknown value: avoided unless the strategy
    # includes them, never costed as free floor.
    known = COIN_TILES | SPIKE_TILES | BASE_CHALLENGE_TILES | KEY_TILES | DOOR_TILES | {WALL, TREASURE, START, "normal"}
    unknown_tiles = sorted({cell for row in grid for cell in row
                            if cell not in known and re.fullmatch(r"c\d+", cell)})
    CHALLENGE_TILES.update(unknown_tiles)
    if unknown_tiles:
        print(f"NOTE: unknown tile ids {unknown_tiles} treated as challenges (500 points, 1 damage, confidence 0.5)")

    scoring = dict(DEFAULT_SCORING)
    scoring.update(map_object.get("defaults") or {})
    scoring.update(body.get("defaults") or {})

    tile_cfg = {tile: dict(cfg) for tile, cfg in DEFAULT_TILE_CONFIG.items()}
    for source in (map_object.get("tileOverrides"), body.get("tile_overrides"), body.get("tileOverrides")):
        for tile, override in (source or {}).items():
            tile_cfg.setdefault(tile, {"points": 0, "damage": 0})
            if isinstance(override, dict):
                tile_cfg[tile].update(
                    {k: v for k, v in override.items() if k in ("points", "damage")}
                )

    for tile in unknown_tiles:
        tile_cfg.setdefault(tile, {"points": 500, "damage": 1})

    confidence = dict(DEFAULT_CONFIDENCE)
    for tile in unknown_tiles:
        confidence.setdefault(tile, 0.5)
    confidence.update(body.get("challenge_confidence") or {})
    confidence.update(strategy_overrides(body.get("strategy")))

    lives = int(body.get("lives") or scoring.get("lives") or 5)
    life_value = int(scoring.get("livesBonusMultiplier", 250))
    treasure_bonus = int(scoring.get("treasureBonus", 1000))
    strategy = normalize_strategy(body.get("strategy"))
    start = _resolve_start(grid, body, map_object)

    treasures = _find(grid, lambda cell: cell == TREASURE)
    if not treasures:
        raise ValueError("no treasure tile on map")
    treasure = min(
        treasures, key=lambda pos: _walk_distance(grid, start, pos) or 10**6
    )

    # Game clock. The prompt the engine sends has no time limit, so it comes
    # from the request, the map object or the navigation prompt text; otherwise
    # the practice-board default applies.
    time_limit = float(
        body.get("time_limit")
        or map_object.get("timeLimit")
        or strategy_time_limit(body.get("strategy"))
        or DEFAULT_TIME_LIMIT
    )
    seconds_per_challenge = float(body.get("seconds_per_challenge") or CHALLENGE_SECONDS)
    nav_overhead_seconds = float(body.get("nav_overhead_seconds") or NAV_OVERHEAD_SECONDS)
    step_seconds = float(body.get("step_seconds") or STEP_SECONDS)
    time_budget = time_limit * TIME_SAFETY
    hinted = strategy_max_challenges(body.get("strategy"))
    if body.get("max_challenges") is not None:
        max_challenges = int(body["max_challenges"])
    elif hinted is not None:
        max_challenges = max(1, hinted)
    else:
        usable = max(0.0, time_budget - nav_overhead_seconds)
        max_challenges = max(1, int(usable // seconds_per_challenge))

    def _estimate_seconds(candidate: Sequence[Coord]) -> float:
        """Clock model for a route: fixed overhead, steps, one turn per graded tile."""
        seconds = nav_overhead_seconds + max(0, len(candidate) - 1) * step_seconds
        seen: Set[Coord] = set()
        for pos in candidate:
            if pos in seen:
                continue
            seen.add(pos)
            cell = grid[pos[0]][pos[1]]
            if cell in CHALLENGE_TILES or cell in KEY_TILES or cell in DOOR_TILES:
                seconds += TILE_SECONDS.get(cell, seconds_per_challenge)
        return seconds

    targets, notes, selected_packages = _select_targets(
        grid,
        start,
        treasure,
        strategy,
        tile_cfg=tile_cfg,
        confidence=confidence,
        life_value=life_value,
        treasure_bonus=treasure_bonus,
        lives=lives,
        max_challenges=max_challenges,
        door_threshold=float(body.get("door_threshold") or DEFAULT_DOOR_THRESHOLD),
        min_confidence=float(body.get("min_confidence") or DEFAULT_MIN_CONFIDENCE),
    )

    _PLAN_DEADLINE[0] = main_deadline

    # Build the path, then repair against a damage budget. Accepted key-door
    # packages remain exact-coordinate atomic units. Repair evaluates the score
    # after removing each ordinary target and never lets the generic reserve
    # silently veto a package that the full terminal-branch model found
    # profitable and survivable on success.
    safety_factor = float(body.get("safety_factor") or DEFAULT_SAFETY_FACTOR)
    life_reserve = float(body.get("life_reserve") or DEFAULT_LIFE_RESERVE)

    attempt_targets = list(targets)
    path = None
    visited: List[Coord] = []
    unreachable: List[Coord] = []

    def _active_packages(pool: Sequence[Coord]) -> List[Tuple[Coord, Coord]]:
        present = set(pool)
        return [
            package for package in selected_packages
            if package[0] in present and package[1] in present
        ]

    def _without_unit(
        pool: Sequence[Coord], unit: Sequence[Coord]
    ) -> List[Coord]:
        return _remove_target_unit(pool, unit, selected_packages)

    def _evaluate_pool(pool: Sequence[Coord]):
        candidate, candidate_visited, candidate_unreachable = _assemble(
            grid, start, pool, treasure, tile_cfg, life_value, confidence
        )
        if candidate is None:
            return candidate, candidate_visited, candidate_unreachable, None, None
        candidate_probe = simulate_path(
            grid, candidate, lives=lives, tile_cfg=tile_cfg, life_value=life_value,
            treasure_bonus=treasure_bonus, confidence=confidence, mode="expected",
        )
        planning_damage = (
            candidate_probe["damage_certain"]
            + candidate_probe["damage_expected"] * safety_factor
        )
        return (
            candidate,
            candidate_visited,
            candidate_unreachable,
            candidate_probe,
            lives - planning_damage,
        )

    # Repair by MAXIMISING EXPECTED SCORE, not by satisfying a damage budget.
    #
    # A damage-budget loop sacrifices valuable targets to satisfy a heuristic.
    # `simulate_path` already prices risk exactly: death forfeits the life and
    # treasure bonuses while banked points survive. So a target is dropped only
    # when dropping it genuinely raises the expected final score.
    def _score_of(probe_result: Optional[Dict[str, Any]]) -> float:
        if probe_result is None:
            return float("-inf")
        if (
            not probe_result["reached_treasure"]
            or probe_result["died"]
            or probe_result["illegal_step"] is not None
        ):
            return float("-inf")
        return float(probe_result["estimated_score_excluding_token_bonus"])

    for _attempt in range(len(targets) + 2):
        if path is not None and time.time() > main_deadline:
            notes.append("repair loop stopped: time budget")
            break
        path, visited, unreachable, probe, headroom = _evaluate_pool(attempt_targets)
        packages = _active_packages(attempt_targets)
        current_score = _score_of(probe)

        # Removal units: each ordinary target alone, or a key-door pair as one
        # atomic unit so a package can never be orphaned.
        package_coords = {pos for package in packages for pos in package}
        ordinary = [pos for pos in attempt_targets if pos not in package_coords]
        units: List[Tuple[Coord, ...]] = [(pos,) for pos in ordinary]
        units += [tuple(package) for package in packages]
        if not units:
            break

        evaluated_units = []
        for unit in units:
            if time.time() > main_deadline:
                break
            reduced = _without_unit(attempt_targets, unit)
            evaluated = _evaluate_pool(reduced)
            if evaluated[0] is None:
                continue
            evaluated_units.append((unit, reduced, evaluated, _score_of(evaluated[3])))

        if not evaluated_units:
            break

        best = max(evaluated_units, key=lambda item: item[3])
        if best[3] <= current_score + 1e-9:
            # Nothing can be removed without losing score. Keep the full plan,
            # even when the old damage reserve would have complained.
            if current_score > float("-inf") and headroom is not None and headroom < life_reserve:
                notes.append(
                    f"kept all targets: expected score {current_score:.0f} is maximal "
                    f"(planning damage {lives - headroom:.2f} of {lives} lives, "
                    f"death probability {probe['death_probability']:.2f})"
                )
            break

        chosen_unit, attempt_targets, chosen_evaluation, chosen_score = best
        path, visited, unreachable = chosen_evaluation[:3]

        if len(chosen_unit) == 2:
            key, door = chosen_unit
            door_cell = grid[door[0]][door[1]]
            notes[:] = [
                note for note in notes
                if not note.startswith(f"door {door_cell} package included:")
            ]
            notes.append(
                f"package {grid[key[0]][key[1]]}->{door_cell} removed: expected score "
                f"{chosen_score:.0f} beats {current_score:.0f} with it"
            )
        else:
            dropped = chosen_unit[0]
            notes.append(
                f"dropped {grid[dropped[0]][dropped[1]]} at {list(dropped)}: "
                f"expected score {chosen_score:.0f} beats {current_score:.0f} with it"
            )

    # REORDER PASS. The tour visits ordinary targets first and appends each
    # key-door package at the end, so tiles that a door would open cheaply can
    # get reached over spikes instead. Try moving each package (key then door)
    # to every earlier slot and keep the ordering with the best expected score.
    for _round in range(2):
        if time.time() > main_deadline:
            notes.append("reorder pass skipped: time budget")
            break
        improved = False
        for key, door in _active_packages(attempt_targets):
            if time.time() > main_deadline:
                break
            base_eval = _evaluate_pool(attempt_targets)
            base_score = _score_of(base_eval[3])
            rest = [pos for pos in attempt_targets if pos not in (key, door)]
            best_pool, best_score, best_eval = None, base_score, None
            for slot in range(len(rest) + 1):
                pool = rest[:slot] + [key, door] + rest[slot:]
                if pool == list(attempt_targets):
                    continue
                evaluated = _evaluate_pool(pool)
                score = _score_of(evaluated[3])
                if score > best_score + 1e-6:
                    best_pool, best_score, best_eval = pool, score, evaluated
            if best_pool is not None:
                notes.append(
                    f"package {grid[key[0]][key[1]]}->{grid[door[0]][door[1]]} moved earlier: "
                    f"expected score {best_score:.0f} beats {base_score:.0f}"
                )
                attempt_targets = best_pool
                path, visited, unreachable = best_eval[:3]
                improved = True
        if not improved:
            break

    # TOUR PASS. The tour above values risk ordering but can walk far more than
    # needed. A long answer is not free: the model must echo every move, and a
    # very long array is more likely to come back reformatted. Re-sequence the
    # same targets with a distance-matrix tour that respects door/key
    # precedence, and keep it when the expected score is not meaningfully
    # lower and the route is shorter.
    if path is not None and attempt_targets:
        try:
            tour = _optimize_order(
                grid, start, treasure, list(attempt_targets),
                budget=max(0.3, min(float(body.get("tour_seconds") or 2.5), main_deadline - time.time())),
            )
        except Exception as exc:  # noqa: BLE001 - optimisation is best effort
            tour = None
            notes.append(f"tour pass skipped: {exc}")
        if tour:
            base_eval = _evaluate_pool(attempt_targets)
            base_score = _score_of(base_eval[3])
            tour_eval = _evaluate_pool(tour)
            tour_score = _score_of(tour_eval[3])
            if (
                tour_eval[0] is not None
                and base_eval[0] is not None
                and tour_score >= base_score - 40.0
                and len(tour_eval[0]) < len(base_eval[0])
            ):
                notes.append(
                    f"tour pass: {len(base_eval[0]) - 1} -> {len(tour_eval[0]) - 1} steps, "
                    f"expected score {base_score:.0f} -> {tour_score:.0f}"
                )
                attempt_targets = tour
                path, visited, unreachable = tour_eval[:3]

    # TIME-FIT PASS. A route that outlasts the clock forfeits the treasure
    # bonus and everything after the cut-off. While the estimate exceeds the
    # budget, drop the removal unit (target or key-door package) with the
    # smallest expected-score loss per second saved, then re-check.
    if path is not None and attempt_targets:
        for _round in range(len(attempt_targets) + 1):
            estimate = _estimate_seconds(path)
            if estimate <= time_budget:
                break
            if time.time() > deadline:
                notes.append("time-fit pass stopped: planning time budget")
                break
            current_eval = _evaluate_pool(attempt_targets)
            current_score = _score_of(current_eval[3])
            packages = _active_packages(attempt_targets)
            package_coords = {pos for package in packages for pos in package}
            ordinary = [pos for pos in attempt_targets if pos not in package_coords]
            units: List[Tuple[Coord, ...]] = [(pos,) for pos in ordinary]
            units += [tuple(package) for package in packages]
            if not units:
                break
            best_choice = None
            for unit in units:
                if time.time() > deadline:
                    break
                reduced = _without_unit(attempt_targets, unit)
                evaluated = _evaluate_pool(reduced)
                if evaluated[0] is None:
                    continue
                saved = estimate - _estimate_seconds(evaluated[0])
                if saved <= 0.0:
                    continue
                loss = current_score - _score_of(evaluated[3])
                if current_score == float("-inf"):
                    loss = 0.0
                ratio = loss / saved
                candidate = (ratio, -saved, unit, reduced, evaluated)
                if best_choice is None or candidate[:2] < best_choice[:2]:
                    best_choice = candidate
            if best_choice is None:
                break
            _ratio, _neg_saved, unit, attempt_targets, evaluated = best_choice
            path, visited, unreachable = evaluated[:3]
            label = "+".join(grid[pos[0]][pos[1]] for pos in unit)
            notes.append(
                f"time fit: dropped {label} at {[list(pos) for pos in unit]}, "
                f"estimate {estimate:.0f}s -> {_estimate_seconds(path):.0f}s "
                f"(budget {time_budget:.0f}s of {time_limit:.0f}s)"
            )

    def _simulate(candidate: Sequence[Coord]) -> Dict[str, Any]:
        return simulate_path(
            grid, candidate, lives=lives, tile_cfg=tile_cfg, life_value=life_value,
            treasure_bonus=treasure_bonus, confidence=confidence, mode="expected",
        )

    def _is_viable(candidate: Optional[Sequence[Coord]]) -> bool:
        if not candidate:
            return False
        probe = _simulate(candidate)
        return (
            probe["reached_treasure"]
            and not probe["died"]
            and probe["illegal_step"] is None
        )

    # SAFETY NET. Never hand back a route the simulation says loses the run,
    # and never walk through a locked door: that costs five lives, i.e. the
    # whole life bonus, for nothing. Escalate through safer fallbacks:
    #   1. the direct route avoiding every hazard;
    #   2. if the treasure is sealed behind a door, the matching key then that
    #      door then the treasure (only when the door answer is trusted);
    #   3. otherwise collect what is reachable and stop with lives intact. The
    #      run then ends on the clock, keeping the life bonus and banked points.
    if not _is_viable(path):
        if path is not None:
            notes.append("planned route was not survivable, falling back")
        path = None
        candidate = _route(
            grid, start, treasure, consumed=set(), unlocked=set(),
            tile_cfg=tile_cfg, life_value=life_value, selected=set(),
            allow_treasure=True,
        )
        if candidate is not None and _is_viable([start] + candidate):
            path, visited, unreachable = [start] + candidate, [], []
            notes.append("fell back to the direct route avoiding all hazards")
        if path is None:
            keys = _find(grid, lambda cell: cell in KEY_TILES)
            doors = _find(grid, lambda cell: cell in DOOR_TILES)
            options = []
            for door in doors:
                door_cell = grid[door[0]][door[1]]
                if float(confidence.get(door_cell, 0.0)) < float(body.get("door_threshold") or DEFAULT_DOOR_THRESHOLD):
                    continue
                for key in keys:
                    if grid[key[0]][key[1]] != DOOR_TO_KEY[door_cell]:
                        continue
                    candidate, cand_visited, cand_unreachable = _assemble(
                        grid, start, [key, door], treasure, tile_cfg, life_value, confidence
                    )
                    if candidate is not None and _is_viable(candidate):
                        options.append((len(candidate), candidate, cand_visited, cand_unreachable, door_cell))
            if options:
                options.sort(key=lambda item: item[0])
                _len, path, visited, unreachable, door_cell = options[0]
                notes.append(f"treasure is sealed behind {door_cell}: taking its key and door")
        if path is None:
            # Loot only: every ordinary target that is reachable without a door,
            # then stop. Locked doors are BLOCKED in the router, so this path can
            # never touch one.
            open_targets = [
                pos for pos in attempt_targets
                if grid[pos[0]][pos[1]] not in DOOR_TILES and grid[pos[0]][pos[1]] not in KEY_TILES
            ]
            loot_path: List[Coord] = [start]
            loot_visited: List[Coord] = []
            current = start
            consumed_loot: Set[Coord] = set()
            for target in _nearest_order(grid, start, open_targets):
                segment = _route(
                    grid, current, target, consumed=consumed_loot, unlocked=set(),
                    tile_cfg=tile_cfg, life_value=life_value, selected=set(open_targets),
                    allow_treasure=False,
                )
                if segment is None:
                    continue
                loot_path.extend(segment)
                consumed_loot.update(segment)
                loot_visited.append(target)
                current = target
            path, visited, unreachable = loot_path, loot_visited, []
            notes.append("WARNING: treasure unreachable without an unavailable door; "
                         "collecting reachable loot and stopping with lives intact")
            if len(path) == 1:
                raise ValueError("no route to treasure exists from the start position")

    expected = _simulate(path)
    worst = simulate_path(
        grid, path, lives=lives, tile_cfg=tile_cfg, life_value=life_value,
        treasure_bonus=treasure_bonus, confidence=confidence, mode="worst",
    )

    if expected["illegal_step"] is not None:
        raise ValueError(f"planner produced an illegal step at {expected['illegal_step']}")

    counts: Dict[str, int] = {}
    for row in grid:
        for cell in row:
            counts[cell] = counts.get(cell, 0) + 1

    count_tile = str(body.get("count_tile") or "c5")
    estimated_seconds = int(_estimate_seconds(path))
    if estimated_seconds > time_limit:
        notes.append(
            f"WARNING: estimated {estimated_seconds}s exceeds the {time_limit:.0f}s clock"
        )

    for pos in unreachable:
        notes.append(f"unreachable target skipped at {list(pos)}")

    # Flat result only: the engine's primary path regex rejects nested braces.
    directions = _to_directions(path)
    coords = [[r, c] for r, c in path]
    output_format = str(body.get("output_format") or "directions").lower()

    # `answer` is the exact string the Supervisor must emit, so the model copies
    # one field instead of rebuilding an array. A tool result is an input message
    # and is free; only what the model emits counts against the token bonus.
    #
    # IT MUST BE A MINIFIED JSON ARRAY. The live engine requires a JSON array
    # and does not fall back to scraping direction words out of free text: a
    # bare comma-separated list produces zero movement and loses the run.
    wants_coords = output_format.startswith("coord")
    emit = coords if wants_coords else directions
    result: Dict[str, Any] = {
        "answer": json.dumps(emit, separators=(",", ":")),
    }

    diagnostics: Dict[str, Any] = {
        "v": TOOL_VERSION,
        # Fingerprint of the board the engine sent. Compare the practice-run
        # value with the leaderboard-run value in CloudWatch: equal hashes mean
        # the leaderboard plays the same map and only the questions differ.
        "grid_sha1": hashlib.sha1(
            json.dumps(grid, separators=(",", ":")).encode()
        ).hexdigest()[:16],
        "grid_size": [len(grid), len(grid[0]) if grid else 0],
        "start": list(start),
        "strategy_text": str(body.get("strategy") or "")[:200],
        "coords": coords,
        "strategy_used": strategy,
        "steps": len(directions),
        "reaches_treasure": expected["reached_treasure"],
        "expected_points": expected["points"],
        "expected_lives_remaining": expected["lives_remaining"],
        "expected_score": expected["estimated_score_excluding_token_bonus"],
        "worst_case_score": worst["estimated_score_excluding_token_bonus"],
        "worst_case_survives": bool(worst["reached_treasure"] and not worst["died"]),
        "challenges_attempted": expected["challenges"],
        "estimated_seconds": estimated_seconds,
        "time_limit_seconds": int(time_limit),
        "tile_counts": [f"{tile}:{count}" for tile, count in sorted(counts.items())],
        "visited_tiles": [grid[p[0]][p[1]] for p in visited],
        "notes": notes,
    }
    print("DIAGNOSTICS: " + json.dumps(diagnostics))

    if body.get("verbose"):
        result.update(diagnostics)
    return result


def _optimize_order(
    grid: Sequence[Sequence[str]],
    start: Coord,
    treasure: Coord,
    targets: List[Coord],
    budget: float = 2.5,
) -> Optional[List[Coord]]:
    """Shortest-walk ordering of `targets` under door precedence.

    Distances come from Dijkstra on the static grid: walls blocked, spikes and
    non-target challenge tiles very expensive (so they are only crossed when
    nothing else works), doors passable. Hard precedence: a key before its
    door, and a door before any target (or the treasure) that cannot be reached
    from the start without passing that door. Nearest-neighbour seed, then
    or-opt and 2-opt moves until the time budget runs out. Generic: nothing
    here depends on a particular board.
    """
    rows, cols = len(grid), len(grid[0])
    deadline = time.time() + budget
    target_set = set(targets)
    cell = lambda pos: grid[pos[0]][pos[1]]

    def step_cost(pos: Coord) -> float:
        c = cell(pos)
        if c == WALL:
            return BLOCKED
        if c == TREASURE:
            return BLOCKED if pos != treasure else 1.0
        if c in SPIKE_TILES:
            return 250.0
        if (c in CHALLENGE_TILES or c in KEY_TILES) and pos not in target_set:
            # Discouraged, not forbidden: a forced corridor through one is
            # consumed on the first crossing, so a prohibitive weight would
            # only distort every later distance in the matrix.
            return 60.0
        return 1.0

    def dijkstra(src: Coord, allow_treasure: bool = False) -> Dict[Coord, float]:
        dist = {src: 0.0}
        heap = [(0.0, src)]
        while heap:
            d, cur = heapq.heappop(heap)
            if d > dist.get(cur, BLOCKED):
                continue
            if cur == treasure and cur != src:
                continue  # never walk through the treasure
            for dr, dc, _n in DIRECTIONS:
                nxt = (cur[0] + dr, cur[1] + dc)
                if not (0 <= nxt[0] < rows and 0 <= nxt[1] < cols):
                    continue
                w = step_cost(nxt)
                if w == BLOCKED:
                    continue
                nd = d + w
                if nd < dist.get(nxt, BLOCKED):
                    dist[nxt] = nd
                    heapq.heappush(heap, (nd, nxt))
        return dist

    def reachable_without(blocked: Coord) -> Set[Coord]:
        seen = {start}
        stack = [start]
        while stack:
            cur = stack.pop()
            for dr, dc, _n in DIRECTIONS:
                nxt = (cur[0] + dr, cur[1] + dc)
                if not (0 <= nxt[0] < rows and 0 <= nxt[1] < cols) or nxt in seen:
                    continue
                if nxt == blocked or cell(nxt) == WALL:
                    continue
                if cell(nxt) == TREASURE:
                    seen.add(nxt)
                    continue
                seen.add(nxt)
                stack.append(nxt)
        return seen

    nodes = [start] + targets
    index = {pos: i for i, pos in enumerate(nodes)}
    dist_rows = [dijkstra(pos) for pos in nodes]
    n = len(nodes)
    D = [[dist_rows[i].get(nodes[j], 1e7) for j in range(n)] for i in range(n)]
    to_treasure = [dist_rows[i].get(treasure, 1e7) for i in range(n)]

    # Precedence: prereq[t] = set of node indices that must come before t.
    prereq: Dict[int, Set[int]] = {i: set() for i in range(1, n)}
    treasure_req: Set[int] = set()
    doors = [pos for pos in targets if cell(pos) in DOOR_TILES]
    for door in doors:
        di = index[door]
        for key in targets:
            if cell(key) in KEY_TILES and KEY_TO_DOOR.get(cell(key)) == cell(door):
                prereq[di].add(index[key])
        reach = reachable_without(door)
        for pos in targets:
            if pos != door and pos not in reach:
                prereq[index[pos]].add(di)
        if treasure not in reach:
            treasure_req.add(di)

    def valid(order: List[int]) -> bool:
        seen = set()
        for i in order:
            if not prereq[i] <= seen:
                return False
            seen.add(i)
        return treasure_req <= seen

    def length(order: List[int]) -> float:
        total, prev = 0.0, 0
        for i in order:
            total += D[prev][i]
            prev = i
        return total + to_treasure[prev]

    # Nearest-neighbour seed respecting precedence.
    remaining = set(range(1, n))
    order: List[int] = []
    prev, done = 0, set()
    while remaining:
        ready = [i for i in remaining if prereq[i] <= done]
        if not ready:
            return None
        nxt = min(ready, key=lambda i: (D[prev][i], i))
        order.append(nxt)
        done.add(nxt)
        remaining.discard(nxt)
        prev = nxt
    if not valid(order):
        return None
    best = length(order)

    improved = True
    while improved and time.time() < deadline:
        improved = False
        m = len(order)
        # or-opt: move a segment of 1..3 nodes elsewhere
        for seg in (1, 2, 3):
            for i in range(m - seg + 1):
                block = order[i:i + seg]
                rest = order[:i] + order[i + seg:]
                for j in range(len(rest) + 1):
                    if j == i:
                        continue
                    cand = rest[:j] + block + rest[j:]
                    cl = length(cand)
                    if cl < best - 1e-9 and valid(cand):
                        order, best, improved = cand, cl, True
                        break
                if improved or time.time() >= deadline:
                    break
            if improved or time.time() >= deadline:
                break
        if improved or time.time() >= deadline:
            continue
        # 2-opt: reverse a segment
        for i in range(m - 1):
            for j in range(i + 2, m + 1):
                cand = order[:i] + order[i:j][::-1] + order[j:]
                cl = length(cand)
                if cl < best - 1e-9 and valid(cand):
                    order, best, improved = cand, cl, True
                    break
            if improved or time.time() >= deadline:
                break
    return [nodes[i] for i in order]


def _assemble(
    grid: Sequence[Sequence[str]],
    start: Coord,
    targets: Sequence[Coord],
    treasure: Coord,
    tile_cfg: Dict[str, Dict[str, int]],
    life_value: int,
    confidence: Dict[str, float],
) -> Tuple[Optional[List[Coord]], List[Coord], List[Coord]]:
    """Stitch per-segment routes together, keeping treasure blocked until last.

    Targets are attempted in repeated passes. A tile sealed behind a locked door
    is unreachable on the first pass, becomes reachable once the matching key is
    collected, and is therefore retried afterwards instead of being discarded.
    A coin in a walled alcove whose only entrance is a door would otherwise be
    dropped by single-pass assembly, which also strips the key/door package of
    the reward that justified it.
    """
    selected = set(targets)
    consumed: Set[Coord] = set()
    unlocked: Set[str] = set()
    path: List[Coord] = [start]
    current = start
    visited: List[Coord] = []

    pending: List[Coord] = list(targets)
    while pending:
        deferred: List[Coord] = []
        progressed = False
        for target in pending:
            segment = _route(
                grid, current, target, consumed=consumed, unlocked=unlocked,
                tile_cfg=tile_cfg, life_value=life_value, selected=selected,
                allow_treasure=False,
            )
            if segment is None:
                deferred.append(target)
                continue
            for pos in segment:
                path.append(pos)
                cell = grid[pos[0]][pos[1]]
                if pos not in consumed:
                    consumed.add(pos)
                    if cell in KEY_TILES:
                        unlocked.add(KEY_TO_DOOR[cell])
            visited.append(target)
            current = target
            progressed = True
        pending = deferred
        # No key was collected in this pass, so nothing new can open.
        if not progressed:
            break

    unreachable: List[Coord] = pending

    final = _route(
        grid, current, treasure, consumed=consumed, unlocked=unlocked,
        tile_cfg=tile_cfg, life_value=life_value, selected=selected,
        allow_treasure=True,
    )
    if final is None:
        return None, visited, unreachable
    path.extend(final)
    return path, visited, unreachable


def _census(grid: Sequence[Sequence[str]]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for row in grid:
        for cell in row:
            counts[cell] = counts.get(cell, 0) + 1
    return counts


def recall(body: Dict[str, Any]) -> Dict[str, Any]:
    """Answer a question about the map deterministically (counts and positions).

    Used for map-memory questions and for the map part of composite questions:
    "How many c7 tiles are on the map?", "count the c1 + c2 challenges",
    "What was your starting position? [row,col]", "position of the treasure",
    "What challenge type is at position [3,4]?", "number of wall tiles",
    "number of challenge tiles excluding coins and spikes". Anything else
    returns the whole census so the caller can finish the arithmetic.
    """
    grid, map_object = _extract_grid(body.get("game_map") or body.get("grid"))
    question = str(body.get("question") or "").strip()
    q = question.lower()
    counts = _census(grid)
    start = _resolve_start(grid, body, map_object)
    treasures = _find(grid, lambda cell: cell == TREASURE)
    treasure = treasures[0] if treasures else None
    tiles = re.findall(r"\bc\d+\b", q)
    challenge_total = sum(n for cell, n in counts.items()
                          if re.fullmatch(r"c\d+", cell) and cell not in COIN_TILES | SPIKE_TILES)

    answer: Any
    pos = re.search(r"position\s*\[?\s*(\d+)\s*[,\s]\s*(\d+)\s*\]?", q)
    if pos and ("type" in q or "what" in q) and "start" not in q and "treasure" not in q:
        r, c = int(pos.group(1)), int(pos.group(2))
        answer = grid[r][c] if 0 <= r < len(grid) and 0 <= c < len(grid[0]) else "out of bounds"
    elif "start" in q and ("position" in q or "where" in q or "begin" in q):
        answer = f"[{start[0]},{start[1]}]"
    elif "treasure" in q and ("position" in q or "where" in q or "located" in q):
        answer = f"[{treasure[0]},{treasure[1]}]" if treasure else "not found"
    elif tiles:
        answer = sum(counts.get(t, 0) for t in dict.fromkeys(tiles))
    elif "wall" in q:
        answer = counts.get(WALL, 0)
    elif "spike" in q or "trap" in q:
        answer = sum(counts.get(t, 0) for t in SPIKE_TILES)
    elif "coin" in q:
        answer = sum(counts.get(t, 0) for t in COIN_TILES)
    elif "key" in q:
        answer = sum(counts.get(t, 0) for t in KEY_TILES)
    elif "door" in q:
        answer = sum(counts.get(t, 0) for t in DOOR_TILES)
    elif "challenge" in q:
        answer = challenge_total
    elif "normal" in q or "empty" in q or "floor" in q:
        answer = counts.get("normal", 0)
    elif "treasure" in q:
        answer = counts.get(TREASURE, 0)
    else:
        summary = {k: v for k, v in sorted(counts.items())}
        summary["start"] = [start[0], start[1]]
        if treasure:
            summary["treasure"] = [treasure[0], treasure[1]]
        summary["challenges_excluding_coins_and_spikes"] = challenge_total
        return {"answer": json.dumps(summary, separators=(",", ":"))}
    print(f"RECALL v={TOOL_VERSION} q={question[:120]!r} answer={answer!r}")
    return {"answer": str(answer)}


def lambda_handler(event, context):
    """Route planner and map memory for the dungeon game.

    The AI League UI generates this tool's schema from the block below. Without
    it the regenerated tool exposes NO parameters, the model cannot send
    game_map, and the navigation turn stalls with zero output tokens.
    Keep the --- Tool / Description / Parameters --- block intact.

    ---
    Tool: route
    Description: Route calculator and map memory for the dungeon. Navigation: pass game_map, start_label and strategy; emit the returned answer field verbatim and nothing else. Map questions (how many tiles of a type, start or treasure position, tile at a position): pass game_map and the question; reply with the answer field.
    Parameters:
        game_map     (required) - the 2D array of tile ids, a JSON array of rows copied from the message, e.g. [["normal","c7"],["wall","treasure"]]
        start_label  (optional) - the start position copied exactly from the input, e.g. A1 (required for navigation)
        strategy     (optional) - the navigation prompt text, e.g. "use strategy max_points skip c4"
        question     (optional) - a question about the map, e.g. "How many c7 tiles are on the map?"; when given, no route is planned
    ---

    ## Map Definitions
        - "wall": non-walkable (a wall step ends the run)
        - "treasure": +1000, always the last tile
        - "normal" / "start": walkable
        - "c7": Coins +250, no challenge
        - "c8": Spikes, -1 life once per coordinate
        - "c1" Violent Violet +400, "c2" Blue Brain +600, "c3" Memento,
          "c4" Dark Prophet +800, "c5" Bonehead +250, "c18" Healthcare API +500
        - "c6": BOSS (finale only) - skipped by max_points unless "include c6"
        - "c40"/"c41": Red/Green key +50, unlock "c30"/"c31" doors (+1000, fatal without key)

    ## Strategies
        max_points - default; coins, positive-value challenges, key/door packages, treasure
        max_loot   - attempts every challenge including the boss
        no_door    - max_points without doors
        safe       - straight to treasure, no challenges
        get_coins  - coins plus treasure
        swift      - shortest path to treasure
        Add "skip cX cY" or "include cX" to any strategy, "time 65" (seconds on
        the map clock; the route is trimmed to fit it) or "max 4 challenges".

    ## Returns
        answer - the minified JSON array of moves. Emit it verbatim.
    """
    try:
        body = _parse_body(event)
        if body.get("question") or str(body.get("op") or "").lower() in ("recall", "census", "count"):
            return {"statusCode": 200, "body": json.dumps(recall(body))}
        result = plan(body)
        print("RESULT: v=%s answer_chars=%s" % (TOOL_VERSION, len(result.get("answer", ""))))
        return {"statusCode": 200, "body": json.dumps(result)}
    except Exception as exc:  # noqa: BLE001 - tool must always answer
        print(f"ERROR: v={TOOL_VERSION} {exc}")
        return {
            "statusCode": 400,
            "body": json.dumps({"error": str(exc), "v": TOOL_VERSION}),
        }
