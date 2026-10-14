"""webfetch - Dark Prophet (c4) web scraping tool.

Fetches the public page named in the challenge question, strips it to text,
and returns the passages most relevant to the question. Standard library only
(the challenge forbids extra dependencies).

Safety: only http/https, and every resolved address (including redirects)
must be public, so the function cannot be pointed at link-local metadata or
private ranges. Only the URL given in the question should ever be fetched.
"""

import gzip
import hashlib
import ipaddress
import json
import os
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from html.parser import HTMLParser

TOOL_VERSION = "2026-10-12.1"
TIMEOUT = 8
MAX_BYTES = 3_000_000
MAX_RETURN = 3500
CACHE_DIR = "/tmp/webfetch-cache"
UA = "Mozilla/5.0 (compatible; AILeagueAgent/1.0)"
STOP = set("""a an the of to in on for and or is are was were be by with from at as it this that
what which who whom whose when where why how according does do did their there they them its
tell me give please site website page url""".split())


def _parse_body(event):
    if not isinstance(event, dict):
        return {}
    body = event.get("body", event)
    if isinstance(body, str):
        try:
            body = json.loads(body)
        except json.JSONDecodeError:
            return {"url": body}
    return body if isinstance(body, dict) else {}


def _check_host(url):
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError("only public http(s) URLs are allowed")
    port = parts.port or (443 if parts.scheme == "https" else 80)
    for info in socket.getaddrinfo(parts.hostname, port, proto=socket.IPPROTO_TCP):
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global or ip.is_multicast:
            raise ValueError(f"blocked non-public address for {parts.hostname}")


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _check_host(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_OPENER = urllib.request.build_opener(_SafeRedirect)


def normalize_url(raw):
    text = str(raw or "").strip()
    m = re.search(r"https?://[^\s\"'<>)\]]+", text)
    if m:
        text = m.group(0)
    elif text and not text.startswith(("http://", "https://")):
        text = "https://" + text.lstrip("/")
    return text.rstrip(".,;:!?")


def fetch(url):
    key = os.path.join(CACHE_DIR, hashlib.sha1(url.encode()).hexdigest())
    try:
        if os.path.exists(key) and time.time() - os.path.getmtime(key) < 1800:
            with open(key, encoding="utf-8") as fh:
                return fh.read()
    except OSError:
        pass
    _check_host(url)
    req = urllib.request.Request(url, headers={
        "User-Agent": UA, "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
        "Accept-Encoding": "gzip, deflate", "Accept-Language": "en-US,en;q=0.8"})
    with _OPENER.open(req, timeout=TIMEOUT) as resp:
        raw = resp.read(MAX_BYTES)
        enc = (resp.headers.get("Content-Encoding") or "").lower()
        charset = resp.headers.get_content_charset() or "utf-8"
    if enc == "gzip":
        raw = gzip.decompress(raw)
    elif enc == "deflate":
        raw = zlib.decompress(raw)
    html = raw.decode(charset, errors="replace")
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(key, "w", encoding="utf-8") as fh:
            fh.write(html)
    except OSError:
        pass
    return html


class _Text(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "template", "iframe"}
    BLOCK = {"p", "div", "li", "br", "tr", "h1", "h2", "h3", "h4", "h5", "h6",
             "section", "article", "header", "footer", "td", "th", "dd", "dt", "blockquote"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self.skip, self.title, self._in_title, self.meta = [], 0, "", False, []

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip += 1
        elif tag == "title":
            self._in_title = True
        elif tag == "meta":
            a = dict(attrs)
            if (a.get("name") or a.get("property") or "").lower() in ("description", "og:description"):
                if a.get("content"):
                    self.meta.append(a["content"])
        if tag in self.BLOCK:
            self.out.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.skip:
            self.skip -= 1
        elif tag == "title":
            self._in_title = False
        if tag in self.BLOCK:
            self.out.append("\n")

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif not self.skip:
            self.out.append(data)


def html_to_text(html):
    p = _Text()
    p.feed(html)
    text = "".join(p.out)
    lines = [re.sub(r"[ \t\r\f\v]+", " ", ln).strip() for ln in text.split("\n")]
    lines = [ln for ln in lines if ln]
    return p.title.strip(), " ".join(p.meta).strip(), lines


def relevant(lines, query, limit=MAX_RETURN):
    words = [w for w in re.findall(r"[a-z0-9]+", str(query or "").lower()) if w not in STOP and len(w) > 2]
    if not words:
        return "\n".join(lines)[:limit]
    scored = []
    for i, ln in enumerate(lines):
        low = ln.lower()
        score = sum(low.count(w) for w in set(words))
        if score:
            scored.append((score, i))
    scored.sort(key=lambda x: (-x[0], x[1]))
    keep = set()
    for _score, i in scored[:12]:
        keep.update(range(max(0, i - 1), min(len(lines), i + 2)))
    picked = "\n".join(lines[i] for i in sorted(keep))
    if len(picked) < limit // 2:
        picked = picked + "\n---\n" + "\n".join(lines)[: limit - len(picked)]
    return picked[:limit]


def lambda_handler(event, context):
    """Web page reader for questions about a specific website.

    ---
    Tool: web
    Description: Fetches the website named in the question and returns the relevant text. Answer the question from the returned text with only the fact asked for.
    Parameters:
        url    (required) - the URL from the question, copied exactly
        query  (required) - the question, used to pick relevant passages
    ---
    """
    started = time.time()
    try:
        body = _parse_body(event)
        url = normalize_url(body.get("url") or body.get("query"))
        if not url:
            raise ValueError("missing url")
        html = fetch(url)
        title, meta, lines = html_to_text(html)
        text = relevant(lines, body.get("query") or "")
        head = " | ".join(x for x in (title, meta) if x)
        result = {"text": (head + "\n" if head else "") + text}
        print(f"WEB v={TOOL_VERSION} url={url} lines={len(lines)} returned={len(result['text'])} "
              f"ms={int((time.time() - started) * 1000)} query={str(body.get('query'))[:200]!r}")
        return {"statusCode": 200, "body": json.dumps(result)}
    except urllib.error.HTTPError as exc:
        print(f"WEB ERROR v={TOOL_VERSION} HTTP {exc.code} {body.get('url')}")
        return {"statusCode": 200, "body": json.dumps({"error": f"HTTP {exc.code}"})}
    except Exception as exc:  # noqa: BLE001
        print(f"WEB ERROR v={TOOL_VERSION} {exc} event={str(event)[:500]}")
        return {"statusCode": 200, "body": json.dumps({"error": str(exc)})}
