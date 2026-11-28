"""Public scholarly discovery and bounded lawful HTML/PDF reading with access labels."""

import asyncio
import hashlib
import io
import ipaddress
import json
import re
import socket
import time
import xml.etree.ElementTree as ET
from datetime import date
from html import unescape
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import httpx
from pypdf import PdfReader

from research import setting

from .store import normalize

_openalex_backoff_until = 0.0
_last_arxiv_request = 0.0


async def search_arxiv(query, start_year, end_year):
    """Public no-DOI fallback. arXiv metadata is not evidence of peer review."""
    global _last_arxiv_request
    stopwords = {
        "the",
        "for",
        "and",
        "what",
        "does",
        "with",
        "from",
        "using",
        "about",
        "study",
        "paper",
        "original",
        "limitations",
    }
    terms = [
        t
        for t in re.findall(r"[\w-]+", query)
        if len(t) > 2 and t.lower() not in stopwords
    ][:7]
    if not terms:
        return []
    await asyncio.sleep(max(0, 3 - (time.monotonic() - _last_arxiv_request)))
    _last_arxiv_request = time.monotonic()
    blob, _, _ = await fetch(
        "https://export.arxiv.org/api/query",
        {
            "search_query": " AND ".join('all:"' + term + '"' for term in terms),
            "start": 0,
            "max_results": 6,
            "sortBy": "relevance",
        },
    )
    ns = {
        "atom": "http://www.w3.org/2005/Atom",
        "arxiv": "http://arxiv.org/schemas/atom",
    }
    rows = []
    for entry in ET.fromstring(blob).findall("atom:entry", ns):
        url = (entry.findtext("atom:id", "", ns)).replace("http://", "https://", 1)
        if not re.match(
            r"https://arxiv\.org/abs/(?:\d{4}\.\d{4,5}|[a-z.-]+/\d{7})(?:v\d+)?$", url
        ):
            continue
        year = int(entry.findtext("atom:published", "0000", ns)[:4])
        if not start_year <= year <= end_year:
            continue
        identifier = url.split("/abs/", 1)[1]
        rows.append(
            {
                "title": normalize(entry.findtext("atom:title", "", ns)),
                "url": url,
                "doi": entry.findtext("arxiv:doi", None, ns),
                "arxiv_id": identifier,
                "year": year,
                "abstract": normalize(entry.findtext("atom:summary", "", ns)),
                "reading_urls": [
                    "https://arxiv.org/pdf/" + identifier,
                    "https://arxiv.org/html/" + identifier,
                ],
                "index": "arXiv",
                "publication_type": "preprint; peer-review status not established",
            }
        )
    return rows


async def public_url(url):
    parsed = urlparse(url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.port not in (None, 443)
    ):
        raise ValueError("Only public HTTPS reading URLs are accepted.")
    addresses = await asyncio.to_thread(
        socket.getaddrinfo, parsed.hostname, 443, type=socket.SOCK_STREAM
    )
    if not addresses or any(
        not ipaddress.ip_address(row[4][0]).is_global for row in addresses
    ):
        raise ValueError("Private/local network addresses are not reading sources.")


async def fetch(url, params=None, max_bytes=12_000_000):
    async with httpx.AsyncClient(
        timeout=18, headers={"User-Agent": "DSAI5205-EvidenceResearch/1.0"}
    ) as client:
        for _ in range(5):
            await public_url(url)
            # Keep credentials out of URLs/logs and never forward them to a redirect host.
            headers = {}
            if urlparse(url).hostname == "api.openalex.org" and setting(
                "OPENALEX_API_KEY"
            ):
                headers["Authorization"] = "Bearer " + setting("OPENALEX_API_KEY")
            async with client.stream(
                "GET", url, params=params, headers=headers
            ) as response:
                if response.is_redirect:
                    url = urljoin(str(response.url), response.headers["location"])
                    params = None
                    continue
                response.raise_for_status()
                chunks, size = [], 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > max_bytes:
                        raise ValueError("Source exceeds the reading size budget.")
                    chunks.append(chunk)
                return (
                    b"".join(chunks),
                    response.headers.get("content-type", ""),
                    str(response.url),
                )
        raise ValueError("Too many source redirects.")


async def search(query, start_year=None, end_year=None):
    global _openalex_backoff_until
    if not isinstance(query, str) or not 3 <= len(query.strip()) <= 180:
        raise ValueError("Use a focused 3–180 character search query.")
    end_year = date.today().year if end_year is None else end_year
    start_year = 1900 if start_year is None else start_year
    if not 1900 <= start_year <= end_year <= date.today().year:
        raise ValueError("Invalid publication window.")
    rows, errors = [], []
    # OA discovery first supplies actual reading URLs; Crossref is an independent fallback.
    try:
        if time.monotonic() < _openalex_backoff_until:
            raise ValueError("OpenAlex temporarily skipped after rate limiting")
        blob, _, _ = await fetch(
            "https://api.openalex.org/works",
            {
                "search": query,
                "per_page": 6,
                "filter": f"publication_year:{start_year}-{end_year}",
                "select": "id,doi,title,publication_year,abstract_inverted_index,open_access,primary_location,best_oa_location",
            },
        )
        for item in json.loads(blob).get("results", []):
            index = item.get("abstract_inverted_index") or {}
            words = sorted(
                (position, word)
                for word, places in index.items()
                for position in places
            )
            locations = [
                item.get("best_oa_location") or {},
                item.get("primary_location") or {},
            ]
            urls = [
                loc[key]
                for loc in locations
                for key in ("pdf_url", "landing_page_url")
                if loc.get(key)
            ]
            oa = (item.get("open_access") or {}).get("oa_url")
            if oa:
                urls.insert(0, oa)
            rows.append(
                {
                    "title": item["title"],
                    "doi": item.get("doi"),
                    "url": item.get("doi") or item["id"],
                    "year": item.get("publication_year"),
                    "abstract": " ".join(w for _, w in words),
                    "reading_urls": list(
                        dict.fromkeys(u for u in urls if u.startswith("https://"))
                    ),
                    "index": "OpenAlex",
                    "publication_type": "index record; peer-review status not independently verified",
                }
            )
    except (httpx.HTTPError, OSError, ValueError, KeyError) as exc:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        if status == 429:
            _openalex_backoff_until = time.monotonic() + 90
        errors.append("OpenAlex: " + (str(status) if status else type(exc).__name__))
    if not rows:
        try:
            blob, _, _ = await fetch(
                "https://api.crossref.org/works",
                {
                    "query": query,
                    "rows": 6,
                    "filter": f"from-pub-date:{start_year}-01-01,until-pub-date:{end_year}-12-31",
                },
            )
            for item in json.loads(blob).get("message", {}).get("items", []):
                if not item.get("DOI") or not item.get("title"):
                    continue
                rows.append(
                    {
                        "title": item["title"][0],
                        "doi": "https://doi.org/" + item["DOI"],
                        "url": "https://doi.org/" + item["DOI"],
                        "year": (item.get("issued", {}).get("date-parts") or [[None]])[
                            0
                        ][0],
                        "abstract": unescape(
                            re.sub(r"<[^>]+>", " ", item.get("abstract") or "")
                        ),
                        "reading_urls": [
                            link["URL"]
                            for link in item.get("link", [])
                            if link.get("URL", "").startswith("https://")
                        ],
                        "index": "Crossref",
                        "publication_type": item.get("type", "unknown"),
                        "related_dois": [
                            r["id"]
                            for kind, relations in item.get("relation", {}).items()
                            if kind
                            in (
                                "is-preprint-of",
                                "has-preprint",
                                "is-version-of",
                                "has-version",
                            )
                            for r in relations
                            if r.get("id-type") == "doi" and r.get("id")
                        ],
                    }
                )
        except (httpx.HTTPError, OSError, ValueError, KeyError) as exc:
            errors.append("Crossref: " + type(exc).__name__)
    if (
        not rows
        or not any(row.get("reading_urls") for row in rows)
        or not any(row.get("index") == "OpenAlex" for row in rows)
    ):
        try:
            rows = await search_arxiv(query, start_year, end_year) + rows
        except (httpx.HTTPError, OSError, ValueError, ET.ParseError) as exc:
            errors.append("arXiv: " + type(exc).__name__)
    return rows, errors


class ArticleParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.blocked = 0
        self.tag = None
        self.parts = []
        self.heading = "body"
        self.rows = []

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "nav", "footer", "noscript"):
            self.blocked += 1
        if (
            not self.blocked
            and tag in ("p", "h1", "h2", "h3", "li")
            and self.tag is None
        ):
            self.tag, self.parts = tag, []

    def handle_endtag(self, tag):
        if tag == self.tag:
            text = normalize(" ".join(self.parts))
            if tag.startswith("h"):
                self.heading = text[:180]
            elif len(text) >= 50:
                self.rows.append({"text": text, "locator": "section: " + self.heading})
            self.tag = None
        if tag in ("script", "style", "nav", "footer", "noscript"):
            self.blocked = max(0, self.blocked - 1)

    def handle_data(self, data):
        if self.tag and not self.blocked:
            self.parts.append(data)


def split_rows(rows):
    chunks = []
    for row in rows:
        text = normalize(row["text"])
        for start in range(0, len(text), 1400):
            piece = text[start : start + 1400]
            if len(piece) >= 40:
                chunks.append(
                    {
                        "text": piece,
                        "locator": row["locator"]
                        + f"; characters {start}–{start + len(piece)}",
                    }
                )
    return chunks[:500]


async def read_source(store, source_id, focus=""):
    source = store.data["sources"].get(source_id)
    if not source:
        raise ValueError(
            "Unknown source ID; URLs cannot be supplied directly to the reader."
        )
    doc = store.data["documents"].get(source_id)
    attempts = []
    if not doc or doc["access"] != "full_text":
        for url in source.get("reading_urls", [])[:2]:
            try:
                async with asyncio.timeout(22):
                    blob, mime, resolved = await fetch(url)
                if "pdf" in mime or blob.startswith(b"%PDF"):
                    pdf = PdfReader(io.BytesIO(blob))
                    rows = [
                        {
                            "text": page.extract_text() or "",
                            "locator": f"PDF page {i + 1}",
                        }
                        for i, page in enumerate(pdf.pages[:60])
                    ]
                elif "html" in mime:
                    parser = ArticleParser()
                    parser.feed(blob.decode("utf-8", errors="replace"))
                    rows = parser.rows
                else:
                    continue
                rows = split_rows(rows)
                body = " ".join(row["text"] for row in rows)
                sections = set(
                    re.findall(
                        r"\b(?:abstract|introduction|methods?|results?|discussion|conclusions?|references)\b",
                        body.lower(),
                    )
                )
                if len(body) < 2000 or len(sections) < 2:
                    raise ValueError(
                        "Article-body checks failed; landing pages are not treated as full text."
                    )
                doc = store.document(
                    source_id,
                    rows,
                    "full_text",
                    resolved,
                    hashlib.sha256(blob).hexdigest(),
                )
                break
            except (httpx.HTTPError, OSError, ValueError, TimeoutError) as exc:
                attempts.append(type(exc).__name__)
        if not doc:
            abstract = normalize(source.get("abstract", ""))
            rows = split_rows([{"text": abstract, "locator": "deposited abstract"}])
            doc = store.document(
                source_id,
                rows,
                "abstract" if rows else "metadata_only",
                source["url"],
                hashlib.sha256(abstract.encode()).hexdigest(),
            )
    tokens = set(re.findall(r"[a-z]{3,}", focus.lower()))
    ranked = sorted(
        doc["passages"],
        key=lambda p: len(tokens & set(re.findall(r"[a-z]{3,}", p["text"].lower()))),
        reverse=True,
    )[:6]
    return {
        **doc,
        "passages": ranked,
        "title": source["title"],
        "attempt_errors": attempts,
        "note": "Full text means extractable body, not proof of complete extraction. HTML navigation may remain; PDF formulas/tables may lose layout. Scanned PDFs are not OCRed.",
    }
