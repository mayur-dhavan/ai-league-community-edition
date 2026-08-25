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

import heapq
import json
import re
from collections import deque
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

Coord = Tuple[int, int]
Package = Tuple[Coord, Coord]

# Bump on every edit. Two live runs produced byte-identical 77 move paths across
# two different versions of this file, which proved the deployed Lambda was stale
# and that both fixes in between had never executed. This marker is printed to
# CloudWatch on every call and returned as "v" under verbose, so "is my code
# actually live?" gets answered by evidence instead of assumption.
TOOL_VERSION = "2026-08-25.5-score-repair"


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
CHALLENGE_TILES = {"c1", "c2", "c3", "c4", "c5", "c6", "c17", "c18"}
KEY_TILES = {"c40", "c41", "c42", "c43"}
DOOR_TILES = {"c30", "c31", "c32", "c33"}
KEY_TO_DOOR = {"c40": "c30", "c41": "c31", "c42": "c32", "c43": "c33"}
DOOR_TO_KEY = {door: key for key, door in KEY_TO_DOOR.items()}

# Values from game-data.md. Override per request when the in-game guide differs.
DEFAULT_TILE_CONFIG: Dict[str, Dict[str, int]] = {
    "c1": {"points": 400, "damage": 1},
    "c2": {"points": 750, "damage": 1},
    "c3": {"points": 750, "damage": 1},
    "c4": {"points": 750, "damage": 1},
    "c5": {"points": 250, "damage": 1},
    "c6": {"points": 1000, "damage": 2},
    "c7": {"points": 2500, "damage": 0},
    "c8": {"points": 0, "damage": 1},
    "c17": {"points": 750, "damage": 2},
    "c18": {"points": 600, "damage": 1},
    "c30": {"points": 1000, "damage": 5},
    "c31": {"points": 1000, "damage": 5},
    "c32": {"points": 1000, "damage": 5},
    "c33": {"points": 1000, "damage": 5},
    "c40": {"points": 50, "damage": 0},
    "c41": {"points": 50, "damage": 0},
    "c42": {"points": 50, "damage": 0},
    "c43": {"points": 50, "damage": 0},
}

DEFAULT_SCORING = {
    "lives": 5,
    "livesBonusMultiplier": 250,
    "tokenBonus": 1000,
    "treasureBonus": 3000,
}

# Probability the agent answers each tile correctly. The red door uses the
# user-selected leaderboard policy: its deterministic key/count pipeline is
# estimated at 0.90, above the one-spike 0.84 break-even. Other doors remain
# opt-in until their contracts are known.
DEFAULT_CONFIDENCE: Dict[str, float] = {
    "c1": 0.97,   # VioletVaultGuard verified in the live game; override to 0.0 to disable
    "c2": 0.97,   # course_optimizer
    "c3": 0.97,   # claims_eob_solver
    "c4": 0.97,   # deterministic registry_lookup exact-fact answer
    "c5": 0.99,   # Haiku 4.5 bare-answer rule for simple questions
    "c6": 0.50,   # unknown boss requirements
    "c17": 0.50,
    "c18": 0.97,  # permit_calculator
    "c30": 0.90,  # red deterministic package; one-spike break-even is 0.84
    "c31": 0.0,
    "c32": 0.0,
    "c33": 0.0,
    "c40": 0.95,
    "c41": 0.95,
    "c42": 0.95,
    "c43": 0.95,
}

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
    """Return (grid, map_object). Accepts a raw 2D array, a JSON string, or a
    full map document with grid/playerStart/defaults/tileOverrides."""
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
    grid = [list(row) + ["normal"] * (width - len(row)) for row in raw]
    return grid, map_object


def normalize_strategy(raw: Any) -> str:
    """Map free-text navigation prompts onto a known strategy name."""
    text = str(raw or "").lower()
    for name in ("no_door", "no door", "nodoor"):
        if name in text:
            return "no_door"
    if "max_loot" in text or "maxloot" in text or "loot" in text:
        return "max_loot"
    if "max_points" in text or "maxpoints" in text or "max_score" in text or "point" in text:
        return "max_points"
    if "safe" in text:
        return "safe"
    if "coin" in text:
        return "get_coins"
    if "swift" in text or "fast" in text or "quick" in text or "short" in text:
        return "swift"
    return "max_points"


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

    for candidate in (
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
    """Greedy nearest-neighbour ordering. Distance only affects elapsed time,
    so a greedy tour is sufficient; an exact tour buys no extra score."""
    remaining = list(targets)
    ordered: List[Coord] = []
    current = start
    while remaining:
        scored = []
        for target in remaining:
            dist = _walk_distance(grid, current, target)
            scored.append((dist if dist is not None else 10**6, target))
        scored.sort()
        _, chosen = scored[0]
        ordered.append(chosen)
        remaining.remove(chosen)
        current = chosen
    return ordered


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
    final score is calculated. Worst and best mode force every branch to fail
    or succeed respectively.
    """
    rows, cols = len(grid), len(grid[0])
    consumed: Set[Coord] = set()
    unlocked: Set[str] = set()
    # state = probability, banked points, lives left, terminal status
    states: List[Tuple[float, float, float, str]] = [
        (1.0, 0.0, float(lives), "active")
    ]
    challenges = 0
    illegal_step: Optional[List[int]] = None
    damage_certain = 0.0
    damage_expected = 0.0

    def active_probability() -> float:
        return sum(prob for prob, _pts, _hp, status in states if status == "active")

    def grade(cell: str) -> None:
        nonlocal states, damage_expected
        cfg = tile_cfg.get(cell, {"points": 0, "damage": 0})
        success = float(confidence.get(cell, 0.5))
        if mode == "worst":
            success = 0.0
        elif mode == "best":
            success = 1.0
        success = min(1.0, max(0.0, success))

        updated: List[Tuple[float, float, float, str]] = []
        for probability, points, remaining, status in states:
            if status != "active":
                updated.append((probability, points, remaining, status))
                continue
            if success:
                updated.append(
                    (probability * success, points + cfg["points"], remaining, "active")
                )
            failure = 1.0 - success
            if failure:
                after = max(0.0, remaining - cfg["damage"])
                failed_status = "dead" if after <= 0 else "active"
                updated.append((probability * failure, points, after, failed_status))
                damage_expected += probability * failure * cfg["damage"]
        states = updated

    for pos in path:
        if active_probability() <= 0:
            break
        r, c = pos
        if not (0 <= r < rows and 0 <= c < cols) or grid[r][c] == WALL:
            illegal_step = [r, c]
            states = [
                (prob, points, 0.0, "dead" if status == "active" else status)
                for prob, points, _remaining, status in states
            ]
            break

        if pos in consumed:
            continue
        cell = grid[r][c]

        if cell == TREASURE:
            states = [
                (prob, points, remaining, "treasure" if status == "active" else status)
                for prob, points, remaining, status in states
            ]
            consumed.add(pos)
            break

        if cell in COIN_TILES:
            reward = tile_cfg.get(cell, {}).get("points", 0)
            states = [
                (prob, points + reward if status == "active" else points, remaining, status)
                for prob, points, remaining, status in states
            ]
            consumed.add(pos)
            continue

        if cell in SPIKE_TILES:
            hit = tile_cfg.get(cell, {}).get("damage", 1)
            damage_certain += active_probability() * hit
            states = [
                (
                    prob,
                    points,
                    max(0.0, remaining - hit) if status == "active" else remaining,
                    "dead" if status == "active" and remaining - hit <= 0 else status,
                )
                for prob, points, remaining, status in states
            ]
            consumed.add(pos)
            continue

        if cell in DOOR_TILES:
            consumed.add(pos)
            cfg = tile_cfg.get(cell, {"points": 0, "damage": 5})
            if cell not in unlocked:
                damage_certain += active_probability() * cfg["damage"]
                states = [
                    (
                        prob,
                        points,
                        max(0.0, remaining - cfg["damage"])
                        if status == "active" else remaining,
                        "dead"
                        if status == "active" and remaining - cfg["damage"] <= 0
                        else status,
                    )
                    for prob, points, remaining, status in states
                ]
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

    expected_points = sum(prob * points for prob, points, _hp, _status in states)
    expected_lives = sum(prob * max(0.0, hp) for prob, _pts, hp, _status in states)
    treasure_probability = sum(
        prob for prob, _pts, _hp, status in states if status == "treasure"
    )
    death_probability = sum(
        prob for prob, _pts, _hp, status in states if status == "dead"
    )
    expected_score = sum(
        prob
        * (
            points
            + max(0.0, remaining) * life_value
            + (treasure_bonus if status == "treasure" else 0)
        )
        for prob, points, remaining, status in states
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
    if len(scored) > max_challenges:
        notes.append(
            f"challenge budget {max_challenges} reached, {len(scored) - max_challenges} skipped"
        )
        scored = scored[:max_challenges]

    ordered = _nearest_order(grid, start, coins)
    cursor = ordered[-1] if ordered else start
    tiers: Dict[float, List[Coord]] = {}
    for prob, _value, pos in scored:
        tiers.setdefault(round(prob, 2), []).append(pos)
    for prob in sorted(tiers, reverse=True):
        tier = _nearest_order(grid, cursor, tiers[prob])
        ordered += tier
        if tier:
            cursor = tier[-1]

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
    return ordered, notes, packages


def plan(body: Dict[str, Any]) -> Dict[str, Any]:
    """Build a validated, runner-compatible path plan."""
    grid, map_object = _extract_grid(body.get("game_map") or body.get("grid"))
    rows, cols = len(grid), len(grid[0])

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

    confidence = dict(DEFAULT_CONFIDENCE)
    confidence.update(body.get("challenge_confidence") or {})

    lives = int(body.get("lives") or scoring.get("lives") or 5)
    life_value = int(scoring.get("livesBonusMultiplier", 250))
    treasure_bonus = int(scoring.get("treasureBonus", 3000))
    strategy = normalize_strategy(body.get("strategy"))
    start = _resolve_start(grid, body, map_object)

    treasures = _find(grid, lambda cell: cell == TREASURE)
    if not treasures:
        raise ValueError("no treasure tile on map")
    treasure = min(
        treasures, key=lambda pos: _walk_distance(grid, start, pos) or 10**6
    )

    # Time budget: each graded tile costs an agent invocation (up to 90s each).
    time_limit = float(body.get("time_limit") or map_object.get("timeLimit") or 300)
    # Measured on a real board: the navigation turn costs about 16s, each graded
    # tile about 13s, and movement itself is close to free.
    seconds_per_challenge = float(body.get("seconds_per_challenge") or 13)
    nav_overhead_seconds = float(body.get("nav_overhead_seconds") or 16)
    step_seconds = float(body.get("step_seconds") or 0.05)
    if body.get("max_challenges") is not None:
        max_challenges = int(body["max_challenges"])
    else:
        usable = max(0.0, time_limit * 0.85 - nav_overhead_seconds)
        max_challenges = max(1, int(usable // seconds_per_challenge))

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
    # The old loop tried to push `certain + expected*2.5` under `lives - 1`. On
    # the live 9x9 board that budget was unreachable (two forced spikes plus the
    # door came to 4.25 of 5 lives), so the loop sacrificed whatever was still
    # droppable to chase it - and after challenges were protected the only things
    # left were the two 2500 point coins. It threw away 5000 points to satisfy a
    # heuristic. `simulate_path` already prices risk exactly: death forfeits the
    # life and treasure bonuses while banked points survive. So a target is
    # dropped only when dropping it genuinely raises the expected final score.
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

    # SAFETY NET. Never hand back a route the simulation says loses the run.
    # A real board produced reaches_treasure=false with expected_score=0 and the
    # engine executed it anyway, scoring zero. Escalate through safer fallbacks.
    if not _is_viable(path):
        if path is not None:
            notes.append("planned route was not survivable, falling back")
        for label, kwargs in (
            ("direct route avoiding all hazards", {}),
            ("direct route allowing locked doors", {"allow_locked_doors": True}),
        ):
            candidate = _route(
                grid, start, treasure, consumed=set(), unlocked=set(),
                tile_cfg=tile_cfg, life_value=life_value, selected=set(),
                allow_treasure=True, **kwargs,
            )
            if candidate is None:
                continue
            candidate = [start] + candidate
            if _is_viable(candidate):
                path, visited, unreachable = candidate, [], []
                notes.append(f"fell back to {label}")
                break
            if path is None:
                path = candidate
        else:
            if path is None:
                raise ValueError("no route to treasure exists from the start position")
            notes.append("WARNING: no survivable route found; returning best effort")

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
    estimated_seconds = int(
        nav_overhead_seconds
        + len(path) * step_seconds
        + expected["challenges"] * seconds_per_challenge
    )

    for pos in unreachable:
        notes.append(f"unreachable target skipped at {list(pos)}")

    # Flat result only: the engine's primary path regex rejects nested braces.
    directions = _to_directions(path)
    coords = [[r, c] for r, c in path]
    output_format = str(body.get("output_format") or "directions").lower()

    # KEEP THIS RESPONSE TINY.
    # The supervisor echoes the tool result as its own answer, so every field
    # here becomes an OUTPUT token and lands in the token-bonus denominator.
    # A verbose response measured 1406 tokens on a real run and zeroed a 1000
    # point bonus. Diagnostics go to CloudWatch instead, or to the caller only
    # when verbose is explicitly requested.
    result: Dict[str, Any] = {
        # Directions are the format the stock game tool emits, so they are the
        # safest default. Switch with output_format="coords" if a run shows the
        # engine prefers absolute positions.
        "path": coords if output_format.startswith("coord") else directions,
        "start_position": [start[0], start[1]],
        count_tile: number_to_words(counts.get(count_tile, 0)),
    }

    diagnostics: Dict[str, Any] = {
        "v": TOOL_VERSION,
        "coords": coords,
        "strategy_used": strategy,
        "steps": len(path),
        "reaches_treasure": expected["reached_treasure"],
        "expected_points": expected["points"],
        "expected_lives_remaining": expected["lives_remaining"],
        "expected_score": expected["estimated_score_excluding_token_bonus"],
        "worst_case_score": worst["estimated_score_excluding_token_bonus"],
        "worst_case_survives": bool(worst["reached_treasure"] and not worst["died"]),
        "challenges_attempted": expected["challenges"],
        "estimated_seconds": estimated_seconds,
        "tile_counts": [f"{tile}:{count}" for tile, count in sorted(counts.items())],
        "visited_tiles": [grid[p[0]][p[1]] for p in visited],
        "notes": notes,
    }
    print("DIAGNOSTICS: " + json.dumps(diagnostics))

    if body.get("verbose"):
        result.update(diagnostics)
    return result


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
    A live board hid a 2500 point coin in a walled alcove whose only entrance was
    the red door; single-pass assembly dropped that coin, which also stripped the
    key/door package of the reward that justified it, so the planner skipped the
    key, the door, and the coin together.
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


def lambda_handler(event, context):
    """Route planner. Returns path, start_position and the c5 tile census.

    The AI League UI generates this tool's schema from the block below. Without
    it the regenerated tool exposes NO parameters, the model cannot send
    game_map, and the navigation turn stalls with zero output tokens.
    Keep the --- Tool / Description / Parameters --- block intact.

    ---
    Tool: route
    Description: Route calculator. Finds the score-optimal path from start to treasure and returns the c5 tile count.
    Parameters:
        game_map     (required) - the 2D grid array as JSON string or list
        start_pos    (optional) - starting position as [row, col], defaults to the "start" cell
        strategy     (required) - routing mode: max_points, max_loot, no_door, safe, get_coins, swift
    ---

    ## Map Definitions
        - "wall": non-walkable cell
        - "treasure": target cell to reach, always last
        - "normal": walkable cell with no special properties
        - "start": the start cell of your avatar, acts as normal cell
        - "c7": Coins, +2500, collected with no challenge
        - "c8": Spikes, -1 life once per coordinate
        - "c1","c2","c3","c4","c5","c18": graded challenge tiles
        - "c40": Red Key, unlocks "c30"
        - "c30": Red Door, fatal without its key

    ## Strategies
        max_points - default; coins, positive-value challenges, then key/door
        max_loot   - attempts every challenge
        no_door    - max_points with the door excluded
        safe       - straight to treasure, no challenges
        get_coins  - coins plus treasure
        swift      - shortest path to treasure

    ## Returns
        path, start_position, c5
    """
    try:
        body = _parse_body(event)
        result = plan(body)
        print(
            "RESULT: v=%s steps=%s keys=%s"
            % (TOOL_VERSION, len(result.get("path", [])), ",".join(result))
        )
        return {"statusCode": 200, "body": json.dumps(result)}
    except Exception as exc:  # noqa: BLE001 - tool must always answer
        print(f"ERROR: v={TOOL_VERSION} {exc}")
        return {
            "statusCode": 400,
            "body": json.dumps({"error": str(exc), "v": TOOL_VERSION}),
        }
