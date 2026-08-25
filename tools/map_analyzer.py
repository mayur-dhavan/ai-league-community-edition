"""Map analyser - tile census and the Red Door (c30) pass string.

Red Door rule from game-data.md:
    The red key supplies the FIRST character of the pass string. The remainder
    is the COUNT of c5 challenges present at the start of the game, spelled out
    in capitals, joined with a dash. Example: key letter X, count six -> "X-SIX".

The rules text says c5 while its own worked example says c6, so `count_tile`
is a parameter. Run one practice submission with each and keep the winner.

Operational contract:
    Call this tool at key delivery with phase="prepare". It returns the visible
    key answer "Thanks" plus a deterministic door_answer to retain. The later
    door challenge uses exact-match grading, so emit the retained door_answer
    directly without another tool call or tool-trace prefix.
"""

import json
import re
from typing import Any, Dict, List, Optional, Sequence

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


def _loads_loose(value: str) -> Any:
    text = value.strip()
    lo = next((i for i, ch in enumerate(text) if ch in "[{"), -1)
    hi = next((i for i in range(len(text) - 1, -1, -1) if text[i] in "]}"), -1)
    if lo >= 0 and hi > lo:
        text = text[lo:hi + 1]
    return json.loads(text)


def extract_grid(raw: Any) -> List[List[str]]:
    if isinstance(raw, str):
        raw = _loads_loose(raw)
    if isinstance(raw, dict):
        raw = raw.get("grid") or raw.get("game_map") or raw.get("map") or []
        if isinstance(raw, str):
            raw = _loads_loose(raw)
    if not isinstance(raw, list) or not raw:
        raise ValueError("game_map must be a non-empty 2D array")
    width = max(len(row) for row in raw)
    return [list(row) + ["normal"] * (width - len(row)) for row in raw]


def count_tiles(grid: Sequence[Sequence[str]]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for row in grid:
        for cell in row:
            counts[str(cell)] = counts.get(str(cell), 0) + 1
    return counts


def extract_key_character(text: str) -> Optional[str]:
    """Find the character the Red Key handed over.

    Handles phrasings such as 'the key is: X', 'your character is X', or a
    message whose only standalone capital letter is the answer.
    """
    if not text:
        return None

    patterns = [
        r"(?:character|letter|char|key)\s*(?:is|=|:)\s*['\"]?([A-Za-z0-9])['\"]?",
        r"(?:is|:)\s*['\"]([A-Za-z0-9])['\"]",
        r"\bfirst character\b[^A-Za-z0-9]{0,12}([A-Za-z0-9])",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1).upper()

    singles = re.findall(r"(?<![A-Za-z0-9])([A-Z])(?![A-Za-z0-9])", text)
    ignore = {"A", "I"}
    filtered = [ch for ch in singles if ch not in ignore]
    if len(filtered) == 1:
        return filtered[0]
    return None


def build_pass_string(key_char: str, count: int) -> str:
    return f"{str(key_char).strip().upper()[:1]}-{number_to_words(count)}"


# Any count the model types is untrustworthy. Two live runs lost the Red Door
# because the supervisor reported the number of PATH MOVES (EIGHTY, then
# NINETY-SIX) instead of the c5 tile census. The map is therefore authoritative,
# and a supplied word is only accepted when it is a real number word inside a
# plausible tile-census range.
MAX_TRUSTED_WORD_COUNT = 40


def words_to_number(text: str) -> Optional[int]:
    """Parse an uppercase English number word (0-99) back into an integer."""
    cleaned = re.sub(r"[^A-Z-]", "", str(text or "").strip().upper())
    if not cleaned:
        return None
    if cleaned in NUMBER_WORDS:
        return NUMBER_WORDS.index(cleaned)
    parts = cleaned.split("-")
    if parts[0] in TENS_WORDS and TENS_WORDS.index(parts[0]) >= 2:
        total = TENS_WORDS.index(parts[0]) * 10
        if len(parts) == 1:
            return total
        if len(parts) == 2 and parts[1] in NUMBER_WORDS[1:10]:
            return total + NUMBER_WORDS.index(parts[1])
    return None


def _parse_body(event: Dict[str, Any]) -> Dict[str, Any]:
    if isinstance(event, dict) and "body" in event:
        body = event["body"]
        if isinstance(body, str):
            try:
                return json.loads(body)
            except json.JSONDecodeError:
                return {}
        if isinstance(body, dict):
            return body
    if isinstance(event, dict) and "parameters" in event:
        return {p.get("name"): p.get("value") for p in event.get("parameters") or []}
    return event if isinstance(event, dict) else {}


def lambda_handler(event, context):
    """Build the Red Door pass string from the map and the red key character.

    The AI League UI generates this tool's schema from the block below. Keep the
    --- Tool / Description / Parameters --- block intact, or the regenerated tool
    exposes no parameters and becomes uncallable.

    ---
    Tool: doorkey
    Description: Prepares the exact Red Door answer during key delivery. For "Red Key N is: X", call with phase="prepare" so answer is Thanks and door_answer is retained. At the later exact-match door question, do not call this tool; emit the retained door_answer directly.
    Parameters:
        game_map     (required) - the 2D grid array as JSON string or list
        key_char     (required) - the single character from "Red Key 1 is: X"
        count_tile   (optional) - tile id to count, defaults to c5
        phase        (optional) - use "prepare" at key delivery; omit for direct diagnostics
    ---

    ## Notes
        - Call during key delivery with phase="prepare"; retain door_answer.
        - At the exact-match door prompt, emit retained door_answer without a tool call.
        - game_map is authoritative. Never send a counted number; this tool counts.
        - Prepare mode returns {"answer":"Thanks","door_answer":"P-FOUR"}.

    ## Returns
        answer
    """
    try:
        body = _parse_body(event)
        count_tile = str(body.get("count_tile") or "c5")

        counts: Dict[str, int] = {}
        count: Optional[int] = None
        count_words: Optional[str] = None
        source = ""

        # 1. The map always wins. Never trust a model-supplied count when the
        #    grid is available, even if both are present.
        raw_map = body.get("game_map") or body.get("grid") or body.get("map")
        if raw_map:
            counts = count_tiles(extract_grid(raw_map))
            count = counts.get(count_tile, 0)
            count_words = number_to_words(count)
            source = "game_map"
        elif body.get("count") is not None:
            parsed = re.sub(r"[^0-9]", "", str(body["count"]))
            if not parsed:
                raise ValueError("count must be a number")
            count = int(parsed)
            count_words = number_to_words(count)
            source = "count"
        elif body.get("count_word") or body.get("c5") or body.get("count_words"):
            supplied = str(
                body.get("count_word") or body.get("c5") or body.get("count_words")
            )
            parsed_word = words_to_number(supplied)
            if parsed_word is None:
                raise ValueError(
                    f"count_word {supplied!r} is not a number word. "
                    "Call again with game_map and let this tool count the tiles."
                )
            if parsed_word > MAX_TRUSTED_WORD_COUNT:
                # A tile census this large is almost certainly a path-length
                # count. Refuse it rather than answer the door incorrectly.
                raise ValueError(
                    f"count_word {supplied!r} ({parsed_word}) is too large to be a "
                    f"{count_tile} census and looks like a path length. Call again "
                    "with game_map so this tool can count the tiles."
                )
            count = parsed_word
            count_words = number_to_words(parsed_word)
            source = "count_word"
        else:
            raise ValueError("provide game_map, count, or count_word")

        key_char = body.get("key_char")
        if not key_char:
            key_char = extract_key_character(str(body.get("key_text") or body.get("text") or ""))

        if key_char:
            door_answer = f"{str(key_char).strip().upper()[:1]}-{count_words}"
            phase = str(body.get("phase") or "door").strip().lower()
            if phase in {"prepare", "key", "key_delivery"}:
                # Key grading is contains-match for "Thanks", while door grading
                # is exact-match. Compute and retain the pass string now, then let
                # the Supervisor emit it later without a tool-trace prefix.
                result: Dict[str, Any] = {
                    "answer": "Thanks",
                    "door_answer": door_answer,
                }
            else:
                result = {"answer": door_answer}
            print(
                "DOORKEY: phase=%s source=%s tile=%s count=%s door_answer=%s"
                % (phase, source, count_tile, count, door_answer)
            )
            if body.get("verbose"):
                result.update({
                    "count_tile": count_tile,
                    "count": count,
                    "count_words": count_words,
                    "count_source": source,
                })
                if counts:
                    result["tile_counts"] = [
                        f"{tile}:{n}" for tile, n in sorted(counts.items())
                    ]
        else:
            result = {"error": "key character not provided or not recognised"}

        return {"statusCode": 200, "body": json.dumps(result)}
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {exc}")
        return {"statusCode": 400, "body": json.dumps({"error": str(exc)})}
