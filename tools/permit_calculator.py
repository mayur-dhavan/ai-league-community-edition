"""Permit Pro (c18) - deterministic renewable energy metrics.

Rules from game-data.md:
    capacity factors: solar 0.25, onshore_wind 0.35, offshore_wind 0.45
    grid emission factor: 420 gCO2/kWh
    average US home: 10,500 kWh/year
    hours per year: 8,760

    annual_generation_gwh = capacity_mw * capacity_factor * 8760 / 1000
    carbon_offset_tons    = annual_generation_gwh * 420
    homes_powered         = annual_generation_gwh * 1,000,000 / 10,500

Worked example: 100 MW solar ->
    {"annual_generation_gwh":219.0,"carbon_offset_tons":91980,"homes_powered":20857}

Rounding of the two integer fields is ambiguous in the rules (floor and
round both reproduce the worked example). `rounding` defaults to "floor";
switch to "round" on a practice run if a submission grades wrong.
"""

import json
import re
from typing import Any, Dict, Optional, Tuple

HOURS_PER_YEAR = 8760
GRID_EMISSION_G_PER_KWH = 420
HOME_KWH_PER_YEAR = 10500

CAPACITY_FACTORS: Dict[str, float] = {
    "solar": 0.25,
    "onshore_wind": 0.35,
    "offshore_wind": 0.45,
}


def detect_kind(text: str) -> str:
    """Infer the project type from a free-text description."""
    lowered = (text or "").lower()
    if "offshore" in lowered:
        return "offshore_wind"
    if "onshore" in lowered:
        return "onshore_wind"
    if "solar" in lowered or "photovoltaic" in lowered or " pv " in f" {lowered} ":
        return "solar"
    if "wind" in lowered:
        return "onshore_wind"
    return "solar"


def detect_capacity_mw(text: str) -> Optional[float]:
    """Pull the capacity in MW out of a description."""
    if not text:
        return None
    match = re.search(r"(\d[\d,]*(?:\.\d+)?)\s*(?:mw|megawatt)", text, re.IGNORECASE)
    if not match:
        match = re.search(r"(\d[\d,]*(?:\.\d+)?)\s*(?:gw|gigawatt)", text, re.IGNORECASE)
        if match:
            return float(match.group(1).replace(",", "")) * 1000
        return None
    return float(match.group(1).replace(",", ""))


def compute(capacity_mw: float, kind: str, rounding: str = "floor") -> Tuple[float, int, int]:
    """Return (annual_generation_gwh, carbon_offset_tons, homes_powered)."""
    factor = CAPACITY_FACTORS.get(kind)
    if factor is None:
        raise ValueError(f"unknown project type: {kind}")

    gwh = capacity_mw * factor * HOURS_PER_YEAR / 1000
    carbon = gwh * GRID_EMISSION_G_PER_KWH
    homes = gwh * 1_000_000 / HOME_KWH_PER_YEAR

    if rounding == "round":
        carbon_i, homes_i = round(carbon), round(homes)
    else:
        carbon_i, homes_i = int(carbon), int(homes)
    return round(gwh, 2), int(carbon_i), int(homes_i)


def build_answer(capacity_mw: float, kind: str, rounding: str = "floor") -> str:
    """Minified JSON answer string, matching the documented field order."""
    gwh, carbon, homes = compute(capacity_mw, kind, rounding)
    gwh_text = f"{gwh:.1f}" if float(gwh).is_integer() or round(gwh, 1) == gwh else f"{gwh}"
    return (
        '{"annual_generation_gwh":' + gwh_text +
        ',"carbon_offset_tons":' + str(carbon) +
        ',"homes_powered":' + str(homes) + "}"
    )


def _parse_body(event: Dict[str, Any]) -> Dict[str, Any]:
    if isinstance(event, dict) and "body" in event:
        body = event["body"]
        if isinstance(body, str):
            try:
                return json.loads(body)
            except json.JSONDecodeError:
                return {"text": body}
        if isinstance(body, dict):
            return body
    if isinstance(event, dict) and "parameters" in event:
        return {p.get("name"): p.get("value") for p in event.get("parameters") or []}
    return event if isinstance(event, dict) else {}


def lambda_handler(event, context):
    """Return the exact c18 energy-impact answer.

    The AI League UI generates this tool's name and parameter list from the block
    below. Keep it intact.

    ---
    Tool: c18
    Description: Solve c18 renewable energy impact. Send capacity_mw and kind (solar, onshore_wind or offshore_wind). Output the returned answer verbatim.
    Parameters:
        capacity_mw  (required) - project capacity in MW as a number
        kind         (required) - solar, onshore_wind or offshore_wind
    ---

    ## Notes
        - A free-text {text} description is also accepted for direct diagnostics.
        - offshore and onshore are detected before the generic word wind.

    ## Returns
        answer
    """
    try:
        body = _parse_body(event)
        text = str(
            body.get("text") or body.get("question") or body.get("description") or ""
        )

        capacity = body.get("capacity_mw") or body.get("mw")
        capacity_mw = float(capacity) if capacity is not None else detect_capacity_mw(text)
        if capacity_mw is None:
            raise ValueError("capacity_mw not provided and not found in text")

        kind = str(body.get("kind") or body.get("type") or "").strip().lower().replace(" ", "_")
        if kind not in CAPACITY_FACTORS:
            kind = detect_kind(text)

        answer = build_answer(capacity_mw, kind, str(body.get("rounding") or "floor"))
        return {"statusCode": 200, "body": json.dumps({"answer": answer})}
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {exc}")
        return {"statusCode": 400, "body": json.dumps({"error": str(exc)})}
