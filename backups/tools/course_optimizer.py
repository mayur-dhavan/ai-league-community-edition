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
    """Normalise a section object, compact array or pipe row into a dict."""
    if isinstance(section, dict):
        data = dict(section)
    elif isinstance(section, str):
        data = _coerce_pipe_row(section)
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


def _coerce_pipe_row(row: str) -> Dict[str, Any]:
    """Parse one `sectionId|courseName|timeSlot|enrolled|capacity` row.

    `instructor` never affects the result, so the five-field row is canonical and
    cheapest. A six-field row is still accepted in the compact-array order,
    which keeps older payloads working.
    """
    parts = [part.strip() for part in row.split("|")]
    if len(parts) >= 6:
        return {name: parts[i] for i, name in enumerate(FIELDS) if i < len(parts)}
    if len(parts) == 5:
        section_id, course, slot, enrolled, capacity = parts
        return {
            "sectionId": section_id,
            "courseName": course,
            "instructor": "",
            "timeSlot": slot,
            "enrolled": enrolled,
            "capacity": capacity,
        }
    raise ValueError(
        f"section row {row!r} needs 5 fields "
        "sectionId|courseName|timeSlot|enrolled|capacity"
    )


def parse_sections(raw: Any) -> List[Any]:
    """Accept a pipe string, a JSON string, or an already-decoded list."""
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            raise ValueError("sections must not be empty")
        if text[0] in "[{":
            decoded = json.loads(text)
            if isinstance(decoded, dict):
                decoded = decoded.get("sections", decoded)
            if not isinstance(decoded, list):
                raise ValueError("sections must be a list")
            return decoded
        # Semicolon-separated pipe rows, newlines tolerated.
        rows = [r.strip() for r in text.replace("\n", ";").split(";")]
        return [r for r in rows if r]
    if isinstance(raw, list):
        return raw
    raise ValueError("sections must be a list or a string")


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
    """Analyse course sections and return the exact c2 answer.

    The AI League UI generates this tool's name and parameter list from the block
    below. Without it the model has to guess the argument shape, and three live
    runs in a row wasted a duplicate call on `c2` doing exactly that.

    ---
    Tool: course_optimizer
    Description: Solve c2 course utilisation. Send one sections string of semicolon rows sectionId|courseName|timeSlot|enrolled|capacity. Output the returned answer verbatim; it is minified JSON with FlaggedSections, Consolidations and NoAction.
    Parameters:
        sections     (required) - semicolon rows sectionId|courseName|timeSlot|enrolled|capacity
    ---

    ## Notes
        - Five fields per row. instructor never changes the result, so omit it.
        - Example: SEC-101|BIO-201|MWF 9:00AM|12|35;SEC-102|BIO-201|MWF 11:00AM|14|35
        - JSON arrays and section objects still parse, for older payloads.

    ## Returns
        answer
    """
    try:
        body = _parse_body(event)
        sections = parse_sections(body.get("sections"))
        return {"statusCode": 200, "body": json.dumps({"answer": build_answer(sections)})}
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {exc}")
        return {"statusCode": 400, "body": json.dumps({"error": str(exc)})}
