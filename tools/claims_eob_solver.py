"""Claims Creature (c3) - FHIR R4 ExplanationOfBenefit adjudication solver.

Rules from game-data.md:
    Total Allowed = SUM of "eligible" amounts for lines WITHOUT a denied outcome.
    Member Responsibility:
        non-denied line: copay + deductible + (eligible - benefit - copay - deductible)
        denied line:     submitted (member owes the full billed charge)
    Denied Lines = every line with reviewOutcome.decision = "denied",
                   reported with its CPT/HCPCS code and CARC reason code.

Note the non-denied formula collapses algebraically to (eligible - benefit),
which is what this module computes.

Output is minified JSON with two-decimal money values:
    {"TotalAllowed":120.00,"MemberResponsibility":49.00,"DeniedLines":[]}

Token note: pass `rows` (compact arrays) rather than the raw EOB where you can.
Raw FHIR JSON costs roughly 1000 output tokens per challenge; compact rows
cost about 80, and the token bonus is an average over challenges visited.
"""

import json
from typing import Any, Dict, List, Optional, Sequence, Tuple

# Compact row layout
ROW_FIELDS = (
    "code", "submitted", "eligible", "copay", "deductible", "benefit", "denied", "carc",
)

CARC_DESCRIPTIONS = {
    "4": "Procedure inconsistent with modifier",
    "18": "Exact duplicate claim/service",
    "29": "Time limit for filing expired",
    "50": "Non-covered service (not a plan benefit)",
    "96": "Non-covered charge, not separately payable",
    "119": "Benefit maximum reached",
    "151": "Information does not support level of service",
    "197": "Precertification/authorization absent",
}


def _money(value: float) -> float:
    return round(float(value or 0.0) + 0.0, 2)


def _amount_for(item: Dict[str, Any], category: str) -> float:
    """Pull an adjudication amount by category code from a FHIR item."""
    for entry in item.get("adjudication") or []:
        codings = ((entry.get("category") or {}).get("coding")) or []
        for coding in codings:
            if str(coding.get("code", "")).lower() == category:
                return float(((entry.get("amount") or {}).get("value")) or 0.0)
    return 0.0


def _service_code(item: Dict[str, Any]) -> str:
    codings = ((item.get("productOrService") or {}).get("coding")) or []
    for coding in codings:
        code = coding.get("code")
        if code:
            return str(code)
    return ""


def _denial(item: Dict[str, Any]) -> Tuple[bool, str]:
    """Return (is_denied, carc_code) for a FHIR item."""
    outcome = item.get("reviewOutcome") or {}
    decision_codings = ((outcome.get("decision") or {}).get("coding")) or []
    denied = any(
        str(coding.get("code", "")).lower() == "denied" for coding in decision_codings
    )
    carc = ""
    reason_codings = ((outcome.get("reason") or {}).get("coding")) or []
    for coding in reason_codings:
        if coding.get("code"):
            carc = str(coding["code"])
            break
    return denied, carc


def normalize_rows(payload: Any) -> List[Dict[str, Any]]:
    """Accept compact rows, a FHIR item[] array, or a full EOB resource."""
    if isinstance(payload, str):
        payload = json.loads(payload)

    if isinstance(payload, dict):
        payload = payload.get("item") or payload.get("items") or payload.get("rows") or []

    rows: List[Dict[str, Any]] = []
    for entry in payload or []:
        if isinstance(entry, (list, tuple)):
            row = {name: entry[i] for i, name in enumerate(ROW_FIELDS) if i < len(entry)}
            rows.append({
                "code": str(row.get("code", "")),
                "submitted": float(row.get("submitted") or 0.0),
                "eligible": float(row.get("eligible") or 0.0),
                "copay": float(row.get("copay") or 0.0),
                "deductible": float(row.get("deductible") or 0.0),
                "benefit": float(row.get("benefit") or 0.0),
                "denied": bool(row.get("denied")),
                "carc": str(row.get("carc") or ""),
            })
            continue

        if isinstance(entry, dict) and "adjudication" in entry:
            denied, carc = _denial(entry)
            rows.append({
                "code": _service_code(entry),
                "submitted": _amount_for(entry, "submitted"),
                "eligible": _amount_for(entry, "eligible"),
                "copay": _amount_for(entry, "copay"),
                "deductible": _amount_for(entry, "deductible"),
                "benefit": _amount_for(entry, "benefit"),
                "denied": denied,
                "carc": carc,
            })
            continue

        if isinstance(entry, dict):
            rows.append({
                "code": str(entry.get("code", "")),
                "submitted": float(entry.get("submitted") or 0.0),
                "eligible": float(entry.get("eligible") or 0.0),
                "copay": float(entry.get("copay") or 0.0),
                "deductible": float(entry.get("deductible") or 0.0),
                "benefit": float(entry.get("benefit") or 0.0),
                "denied": bool(entry.get("denied")),
                "carc": str(entry.get("carc") or ""),
            })

    return rows


def analyse(payload: Any) -> Dict[str, Any]:
    """Compute totals and denied lines, preserving input line order."""
    rows = normalize_rows(payload)
    total_allowed = 0.0
    member_responsibility = 0.0
    denied_lines: List[Dict[str, str]] = []

    for row in rows:
        if row["denied"]:
            member_responsibility += row["submitted"]
            denied_lines.append({"code": row["code"], "carc": row["carc"]})
            continue
        total_allowed += row["eligible"]
        # copay + deductible + coinsurance collapses to eligible - benefit
        member_responsibility += row["eligible"] - row["benefit"]

    return {
        "TotalAllowed": _money(total_allowed),
        "MemberResponsibility": _money(member_responsibility),
        "DeniedLines": denied_lines,
    }


def build_answer(payload: Any, zero_style: str = "decimal") -> str:
    """Minified JSON string with hand-built two-decimal money values.

    json.dumps would render 1250.00 as 1250.0, so the numbers are formatted
    as text. Grading via json_exact_match compares parsed values, so this is
    also safe there; it only matters if the tile uses exact_match.
    """
    data = analyse(payload)

    def fmt(value: float) -> str:
        if zero_style == "bare" and round(value, 2) == 0:
            return "0"
        return f"{value:.2f}"

    denied = ",".join(
        '{"code":"%s","carc":"%s"}' % (line["code"], line["carc"])
        for line in data["DeniedLines"]
    )
    return (
        '{"TotalAllowed":' + fmt(data["TotalAllowed"]) +
        ',"MemberResponsibility":' + fmt(data["MemberResponsibility"]) +
        ',"DeniedLines":[' + denied + "]}"
    )


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
    """Accepts {"rows":[[...]]}, {"item":[...]}, or a full EOB resource."""
    try:
        body = _parse_body(event)
        payload = (
            body.get("rows")
            or body.get("item")
            or body.get("items")
            or body.get("eob")
            or body.get("resource")
        )
        if payload is None:
            raise ValueError("provide rows or a FHIR item[] array")
        answer = build_answer(payload, str(body.get("zero_style") or "decimal"))
        return {"statusCode": 200, "body": json.dumps({"answer": answer})}
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {exc}")
        return {"statusCode": 400, "body": json.dumps({"error": str(exc)})}
