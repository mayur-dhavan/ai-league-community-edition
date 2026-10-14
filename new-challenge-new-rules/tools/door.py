"""doorkey - deterministic answers for the Red (c30) and Green (c31) doors.

Why a tool: in community runs the model knew the key but lost the door three
times purely on formatting ("Based on the conversation history ... P").
A door miss is -5 lives, i.e. the whole run, so the model must only relay.

Board rules (in-game guide):
  Red door   - translate the code you receive by reading it backwards.
  Green door - translate the code by replacing letters with the numbers
               that represent them, in order (A=1 ... Z=26).

The key turn needs no tool: the agent replies "Thanks". At the door the agent
passes the colour and the code exactly as the earlier key message gave it.
Standard library only.
"""

import json
import re

TOOL_VERSION = "2026-10-12.1"


def _parse_body(event):
    if not isinstance(event, dict):
        return {}
    body = event.get("body", event)
    if isinstance(body, str):
        try:
            body = json.loads(body)
        except json.JSONDecodeError:
            return {}
    return body if isinstance(body, dict) else {}


def extract_code(raw):
    """Accept either the bare code or the whole key message.

    "Red Key 1 is: XK7Q" -> "XK7Q". Quotes and trailing punctuation are stripped.
    """
    text = str(raw or "").strip()
    m = re.search(r"\bis\s*[:=]?\s*(.+)$", text, re.I)
    if m and re.search(r"\bkey\b", text, re.I):
        text = m.group(1).strip()
    text = text.strip().strip("\"'`").rstrip(".!")
    return text.strip()


def red(code):
    return code[::-1]


def green(code, sep=""):
    out = []
    for ch in code:
        if ch.isalpha() and ch.isascii():
            out.append(str(ord(ch.upper()) - 64))
        elif ch.isspace():
            continue
        else:
            out.append(ch)
    return sep.join(out)


def solve(color, code, sep=""):
    color = str(color or "").lower()
    if "green" in color or color in ("c31", "g"):
        return green(code, sep)
    if "red" in color or color in ("c30", "r"):
        return red(code)
    raise ValueError("color must be red or green")


def lambda_handler(event, context):
    """Door solver. Returns the exact door answer.

    ---
    Tool: door
    Description: Solves a Red or Green door. Pass the door colour and the code exactly as the earlier key message gave it. Emit the answer field verbatim and nothing else.
    Parameters:
        color  (required) - red or green
        code   (required) - the key code copied exactly from the key message
    ---
    """
    try:
        body = _parse_body(event)
        code = extract_code(body.get("code") or body.get("key") or body.get("text"))
        if not code:
            raise ValueError("missing code")
        answer = solve(body.get("color") or body.get("colour"), code,
                       str(body.get("sep") or ""))
        print(f"DOOR v={TOOL_VERSION} color={body.get('color')} code={code!r} answer={answer!r}")
        return {"statusCode": 200, "body": json.dumps({"answer": answer})}
    except Exception as exc:  # noqa: BLE001
        print(f"DOOR ERROR v={TOOL_VERSION} {exc} event={event}")
        return {"statusCode": 400, "body": json.dumps({"error": str(exc)})}
