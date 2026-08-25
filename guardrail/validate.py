"""Pre-flight validation for the Violet Vault guardrail payload.

Checks the Bedrock Classic-tier limits before you spend an API call:
  * denied topic definition <= 200 characters
  * sample phrase <= 100 characters, maximum 5 per topic
  * a definition must not begin with "don't"
  * at least one policy present

Usage:  python guardrail/validate.py
"""

import json
import os
import sys

PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "violet-vault-guard.json")

DEFINITION_LIMIT = 200
PHRASE_LIMIT = 100
PHRASE_COUNT_LIMIT = 5
NAME_LIMIT = 100


def main() -> int:
    with open(PATH, encoding="utf-8") as handle:
        payload = json.load(handle)

    problems = []

    name = payload.get("name", "")
    if not name or len(name) > NAME_LIMIT:
        problems.append(f"guardrail name length {len(name)} outside 1-{NAME_LIMIT}")
    if " " in name:
        problems.append("guardrail name contains a space; Bedrock requires [0-9a-zA-Z-_]+")

    for field in ("blockedInputMessaging", "blockedOutputsMessaging"):
        if not payload.get(field):
            problems.append(f"{field} is required")

    policies = [key for key in payload if key.endswith("Config")]
    if not policies:
        problems.append("guardrail needs at least one policy")

    topics = (payload.get("topicPolicyConfig") or {}).get("topicsConfig") or []
    for topic in topics:
        label = topic.get("name", "<unnamed>")
        definition = topic.get("definition", "")
        examples = topic.get("examples") or []

        if len(definition) > DEFINITION_LIMIT:
            problems.append(
                f"{label}: definition is {len(definition)} chars, limit {DEFINITION_LIMIT}"
            )
        if definition.lower().lstrip().startswith(("don't", "dont", "do not")):
            problems.append(f"{label}: definition must not begin with a negation")
        if topic.get("type") != "DENY":
            problems.append(f"{label}: type must be DENY")
        if len(examples) > PHRASE_COUNT_LIMIT:
            problems.append(
                f"{label}: {len(examples)} sample phrases, limit {PHRASE_COUNT_LIMIT}"
            )
        for example in examples:
            if len(example) > PHRASE_LIMIT:
                problems.append(
                    f"{label}: sample phrase is {len(example)} chars, limit {PHRASE_LIMIT}"
                )

        print(
            f"{label:<24} definition={len(definition):>3}/{DEFINITION_LIMIT}  "
            f"phrases={len(examples)}/{PHRASE_COUNT_LIMIT}  "
            f"longest phrase={max((len(e) for e in examples), default=0):>3}/{PHRASE_LIMIT}"
        )

    print(f"\npolicies: {', '.join(policies) or 'none'}")
    print("contentPolicyConfig omitted deliberately: all-NONE filters add no value "
          "and only add validation failure modes.")

    if problems:
        print("\nPROBLEMS:")
        for problem in problems:
            print(f"  - {problem}")
        return 1

    print("\nPayload is within all documented Classic-tier limits.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
