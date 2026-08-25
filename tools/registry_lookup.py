"""Web Weaver (c4) - AWS Registry of Open Data fact lookup.

Grading for this tile is wording-sensitive, so the tool returns a compact
extract of the dataset page and the agent answers with the phrase verbatim -
no sentence, no preamble.

Two sources are tried, cheapest and cleanest first:
  1. the dataset YAML in awslabs/open-data-registry (same wording, far smaller)
  2. the rendered page at registry.opendata.aws/<slug>/

Network access is restricted to those two hosts. Arbitrary URL fetching is
deliberately not supported: an open fetcher inside a Lambda is an SSRF hole
that can reach link-local metadata and anything else in the account.
"""

import json
import os
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from typing import Any, Dict, List, Optional, Tuple

ALLOWED_HOSTS = {"registry.opendata.aws", "raw.githubusercontent.com"}
YAML_BASE = "https://raw.githubusercontent.com/awslabs/open-data-registry/main/datasets"
PAGE_BASE = "https://registry.opendata.aws"

USER_AGENT = "Mozilla/5.0 (compatible; AILeagueAgent/1.0)"
TIMEOUT_SECONDS = 6
MAX_BYTES = 400_000
DEFAULT_MAX_CHARS = 2500
CACHE_DIR = "/tmp/registry_cache"

FIELD_PATTERN = re.compile(
    r"^(Name|Description|Documentation|Contact|ManagedBy|UpdateFrequency|License|Citation|Tags):\s*(.*)$",
    re.IGNORECASE,
)


class _TextExtractor(HTMLParser):
    """Collect visible text, dropping script/style content."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip = 0
        self.chunks: List[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in ("script", "style", "noscript", "svg"):
            self._skip += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style", "noscript", "svg") and self._skip:
            self._skip -= 1

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        text = data.strip()
        if text:
            self.chunks.append(text)

    def text(self) -> str:
        return " ".join(self.chunks)


def slug_candidates(name: str) -> List[str]:
    """Generate plausible registry slugs for a dataset name."""
    raw = (name or "").strip().lower()
    if not raw:
        return []

    cleaned = re.sub(r"[^a-z0-9\s\-_]", "", raw)
    words = [word for word in re.split(r"[\s_\-]+", cleaned) if word]
    stop = {"the", "of", "a", "an", "and", "dataset", "data"}
    meaningful = [word for word in words if word not in stop]

    candidates = [
        raw.replace(" ", "-"),
        "-".join(words),
        "-".join(meaningful),
        "".join(meaningful),
        # Acronyms: registry slugs sometimes keep the leading article (tcga),
        # sometimes drop it (cga), so offer both.
        "".join(word[0] for word in words) if len(words) > 1 else "",
        "".join(word[0] for word in meaningful) if len(meaningful) > 1 else "",
    ]

    ordered: List[str] = []
    for candidate in candidates:
        candidate = re.sub(r"-+", "-", candidate).strip("-")
        if candidate and candidate not in ordered:
            ordered.append(candidate)
    return ordered


def _assert_public_host(url: str) -> None:
    """Allow only the two registry hosts, and only public addresses."""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https":
        raise ValueError("only https is allowed")
    if parsed.hostname not in ALLOWED_HOSTS:
        raise ValueError(f"host not allowed: {parsed.hostname}")
    try:
        infos = socket.getaddrinfo(parsed.hostname, 443)
    except socket.gaierror as exc:
        raise ValueError(f"cannot resolve {parsed.hostname}") from exc
    for info in infos:
        address = info[4][0]
        if address.startswith(("10.", "127.", "169.254.", "192.168.")):
            raise ValueError("resolved to a non-public address")


def _fetch(url: str) -> Optional[str]:
    """Fetch a permitted URL, using /tmp as a warm-container cache."""
    _assert_public_host(url)

    os.makedirs(CACHE_DIR, exist_ok=True)
    cache_path = os.path.join(
        CACHE_DIR, re.sub(r"[^A-Za-z0-9]+", "_", url)[-120:] + ".txt"
    )
    if os.path.exists(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as handle:
                return handle.read()
        except OSError:
            pass

    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            if getattr(response, "status", 200) >= 400:
                return None
            payload = response.read(MAX_BYTES)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError):
        return None

    text = payload.decode("utf-8", errors="replace")
    try:
        with open(cache_path, "w", encoding="utf-8") as handle:
            handle.write(text)
    except OSError:
        pass
    return text


def parse_yaml_fields(text: str) -> Dict[str, Any]:
    """Minimal YAML field reader - avoids a PyYAML dependency in the Lambda."""
    fields: Dict[str, Any] = {}
    tags: List[str] = []
    in_tags = False

    for line in text.splitlines():
        stripped = line.strip()
        if in_tags:
            if stripped.startswith("- "):
                tags.append(stripped[2:].strip())
                continue
            in_tags = False
        if re.match(r"^Tags:\s*$", stripped, re.IGNORECASE):
            in_tags = True
            continue
        match = FIELD_PATTERN.match(stripped)
        if match:
            key, value = match.group(1), match.group(2).strip().strip('"').strip("'")
            if key.lower() == "tags":
                tags.extend(part.strip() for part in value.strip("[]").split(",") if part.strip())
            elif value:
                fields[key] = value

    if tags:
        fields["Tags"] = tags
    return fields


def extract_facts(page_text: str, max_chars: int = DEFAULT_MAX_CHARS) -> str:
    """Trim page text down to the region that answers registry questions."""
    text = re.sub(r"\s+", " ", page_text).strip()
    anchor = text.lower().find("description")
    if anchor > 0:
        text = text[anchor:]
    return text[:max_chars]


def lookup(name: str, slug: Optional[str] = None, max_chars: int = DEFAULT_MAX_CHARS) -> Dict[str, Any]:
    """Resolve a dataset and return its facts plus the source used."""
    candidates = [slug] if slug else slug_candidates(name)
    tried: List[str] = []

    for candidate in candidates:
        if not candidate:
            continue
        tried.append(candidate)

        yaml_text = _fetch(f"{YAML_BASE}/{candidate}.yaml")
        if yaml_text and "Description" in yaml_text:
            fields = parse_yaml_fields(yaml_text)
            if fields:
                return {
                    "slug": candidate,
                    "source": "yaml",
                    "name": fields.get("Name", name),
                    "description": (fields.get("Description") or "")[:max_chars],
                    "managed_by": fields.get("ManagedBy", ""),
                    "update_frequency": fields.get("UpdateFrequency", ""),
                    "license": fields.get("License", ""),
                    "tags": fields.get("Tags", []),
                    "tried": tried,
                }

        page = _fetch(f"{PAGE_BASE}/{candidate}/")
        if page:
            extractor = _TextExtractor()
            extractor.feed(page)
            content = extract_facts(extractor.text(), max_chars)
            if content:
                return {
                    "slug": candidate,
                    "source": "page",
                    "name": name,
                    "content": content,
                    "tried": tried,
                }

    return {"error": "dataset not found", "tried": tried}


def _parse_body(event: Dict[str, Any]) -> Dict[str, Any]:
    if isinstance(event, dict) and "body" in event:
        body = event["body"]
        if isinstance(body, str):
            try:
                return json.loads(body)
            except json.JSONDecodeError:
                return {"name": body}
        if isinstance(body, dict):
            return body
    if isinstance(event, dict) and "parameters" in event:
        return {p.get("name"): p.get("value") for p in event.get("parameters") or []}
    return event if isinstance(event, dict) else {}


def lambda_handler(event, context):
    """Accepts {name}, optional {slug}, optional {max_chars}."""
    try:
        body = _parse_body(event)
        name = str(body.get("name") or body.get("dataset") or body.get("question") or "")
        slug = body.get("slug")
        if not name and not slug:
            raise ValueError("provide a dataset name or slug")
        max_chars = int(body.get("max_chars") or DEFAULT_MAX_CHARS)
        return {"statusCode": 200, "body": json.dumps(lookup(name, slug, max_chars))}
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {exc}")
        return {"statusCode": 400, "body": json.dumps({"error": str(exc)})}
