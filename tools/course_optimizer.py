"""Schedule Sage (c2) - course section utilisation analyser.

The tool name is fixed by game-data.md: "Pass it to the course_optimizer tool".

Rules:
    1. FLAG any section where enrolled / capacity < 0.50. Exactly 50% is NOT flagged.
    2. CONSOLIDATE when all are true:
         - same course
         - both sections flagged
         - combined enrollment fits the kept section's capacity
         - time slots differ (identical slots cannot consolidate)
    3. Keep the higher enrollment, cancel the lower.
    4. NoAction: flagged sections that could not be consolidated.

Output is minified JSON with input ordering preserved:
    {"FlaggedSections":[...],"Consolidations":[...],"NoAction":[...]}
"""

import json
from typing import Any, Dict, List, Sequence

FIELDS = ("sectionId", "courseName", "instructor", "timeSlot", "enrolled", "capacity")


def _coerce(section: Any) -> Dict[str, Any]:
    """Normalise a section object or compact array into a dict."""
    if isinstance(section, dict):
        data = dict(section)
    elif isinstance(section, (list, tuple)):
        data = {name: section[i] for i, name in enumerate(FIELDS) if i < len(section)}
    else:
        raise ValueError(f"unsupported section entry: {section!r}")

    data["sectionId"] = str(data.get("sectionId", "")).strip()
    data["courseName"] = str(data.get("courseName", "")).strip()
    data["timeSlot"] = str(data.get("timeSlot", "")).strip()
    data["enrolled"] = int(data.get("enrolled", 0))
    data["capacity"] = int(data.get("capacity", 0))
    return data


def analyse(sections: Sequence[Any]) -> Dict[str, Any]:
    """Apply the flag/consolidate rules. Input order is preserved throughout."""
    rows = [_coerce(section) for section in sections]

    flagged: List[Dict[str, Any]] = []
    for row in rows:
        capacity = row["capacity"]
        if capacity > 0 and (row["enrolled"] / capacity) < 0.5:
            flagged.append(row)

    flagged_ids = [row["sectionId"] for row in flagged]
    consolidations: List[Dict[str, Any]] = []
    used: set = set()

    for i, left in enumerate(flagged):
        if left["sectionId"] in used:
            continue
        for right in flagged[i + 1:]:
            if right["sectionId"] in used:
                continue
            if left["courseName"] != right["courseName"]:
                continue
            if left["timeSlot"] == right["timeSlot"]:
                continue

            # Keep the higher enrollment; ties keep the earlier input entry.
            keep, cancel = (left, right) if left["enrolled"] >= right["enrolled"] else (right, left)
            combined = left["enrolled"] + right["enrolled"]
            # Defensive only. Given both sections are below 50% utilisation,
            # the kept section's enrollment is the larger of the two, so
            # combined < capacity always holds and this can never trigger.
            # It stays in case a variant relaxes the flag threshold.
            if combined > keep["capacity"]:
                continue

            consolidations.append({
                "keep": keep["sectionId"],
                "cancel": cancel["sectionId"],
                "combinedEnrollment": combined,
                "capacity": keep["capacity"],
            })
            used.add(left["sectionId"])
            used.add(right["sectionId"])
            break

    no_action = [sid for sid in flagged_ids if sid not in used]
    return {
        "FlaggedSections": flagged_ids,
        "Consolidations": consolidations,
        "NoAction": no_action,
    }


def build_answer(sections: Sequence[Any]) -> str:
    """Minified JSON string, no spaces after separators."""
    return json.dumps(analyse(sections), separators=(",", ":"))


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
    """Accepts {"sections":[...]} as objects or compact arrays."""
    try:
        body = _parse_body(event)
        sections = body.get("sections")
        if isinstance(sections, str):
            sections = json.loads(sections)
        if not isinstance(sections, list):
            raise ValueError("sections must be a list")
        return {"statusCode": 200, "body": json.dumps({"answer": build_answer(sections)})}
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {exc}")
        return {"statusCode": 400, "body": json.dumps({"error": str(exc)})}
