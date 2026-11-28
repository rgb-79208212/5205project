"""Run-scoped source/document/evidence registry shared through the MCP server."""

import hashlib
import json
import re
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse


def stable_id(prefix, value):
    return prefix + hashlib.sha256(value.encode()).hexdigest()[:12]


def normalize(text):
    return re.sub(r"\s+", " ", text).strip()


def source_identifiers(row):
    """Identifiers help locate a work; none of them certify its findings."""
    doi = re.sub(
        r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)",
        "",
        row.get("doi") or "",
        flags=re.I,
    ).lower()
    urls = [row["url"], *row.get("reading_urls", [])]
    arxiv = row.get("arxiv_id") or next(
        (
            m.group(1)
            for u in urls
            if urlparse(u).hostname
            in ("arxiv.org", "www.arxiv.org", "export.arxiv.org")
            if (m := re.search(r"arxiv\.org/(?:abs|pdf|html)/([^?#]+)", u))
        ),
        None,
    )
    if not arxiv:
        match = re.fullmatch(r"10\.48550/arxiv\.(.+)", doi, flags=re.I)
        arxiv = match.group(1) if match else None
    if arxiv:
        arxiv = re.sub(r"(?:v\d+)?(?:\.pdf)?$", "", arxiv)
    return {
        "doi": doi or None,
        "arxiv": arxiv,
        "url": row["url"],
        "host": urlparse(row["url"]).hostname,
    }


def study_keys(source):
    identifiers = source.get("identifiers") or source_identifiers(source)
    keys = {
        f"{kind}:{identifiers[kind]}"
        for kind in ("doi", "arxiv")
        if identifiers.get(kind)
    }
    for doi in source.get("related_dois", []):
        keys.add(
            "doi:"
            + re.sub(r"^https?://(?:dx\.)?doi\.org/", "", doi, flags=re.I).lower()
        )
    title = re.sub(r"\W+", "", source["title"]).lower()
    if title:
        keys.add("title:" + title)
    return keys or {"url:" + source["url"]}


class Store:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / "tool-state.json"
        self.data = (
            json.loads(self.path.read_text(encoding="utf-8"))
            if self.path.exists()
            else {
                "sources": {},
                "documents": {},
                "document_versions": {},
                "evidence": {},
            }
        )

    def save(self):
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(self.data, ensure_ascii=False, indent=2, allow_nan=False),
            encoding="utf-8",
        )
        temporary.replace(self.path)

    def add_sources(self, rows):
        added = []
        for row in rows:
            row = dict(row)
            identifiers = source_identifiers(row)
            reading_urls = list(row.get("reading_urls", []))
            direct = []
            if identifiers["arxiv"] and not any(
                urlparse(u).hostname == "arxiv.org" and "/pdf/" in u
                for u in reading_urls
            ):
                reading_id = row.get("arxiv_id") or next(
                    (
                        m.group(1).removesuffix(".pdf")
                        for u in [row["url"], *reading_urls]
                        if urlparse(u).hostname
                        in ("arxiv.org", "www.arxiv.org", "export.arxiv.org")
                        and (m := re.search(r"/(?:abs|pdf|html)/([^?#]+)", u))
                    ),
                    identifiers["arxiv"],
                )
                direct.append("https://arxiv.org/pdf/" + reading_id)
            for url in reading_urls:
                parsed = urlparse(url)
                if parsed.hostname == "openreview.net" and parsed.path == "/forum":
                    identifier = parse_qs(parsed.query).get("id", [""])[0]
                    if identifier:
                        direct.append(
                            "https://openreview.net/pdf?id="
                            + quote(identifier, safe="")
                        )
            row["reading_urls"] = list(dict.fromkeys(direct + reading_urls))
            identity = (row.get("doi") or row["url"]).lower().rstrip("/")
            identifier = stable_id("S", identity)
            title_key = re.sub(r"\W+", "", row["title"]).lower()
            existing = self.data["sources"].get(identifier) or next(
                (
                    s
                    for s in self.data["sources"].values()
                    if re.sub(r"\W+", "", s["title"]).lower() == title_key
                ),
                None,
            )
            if existing:
                identifier = existing["id"]
                reading_urls = list(
                    dict.fromkeys(
                        existing.get("reading_urls", []) + row.get("reading_urls", [])
                    )
                )
                row = {**row, **existing, "reading_urls": reading_urls}
            item = {**row, "id": identifier, "identifiers": source_identifiers(row)}
            item["source_policy"] = {
                "identifier_required": "Stable URL or scholarly identifier; DOI is optional",
                "content_required": "Substantive claims require readable quoted content, not metadata",
                "authority": "Not certified by identifier or search-index inclusion",
            }
            self.data["sources"][identifier] = item
            added.append(item)
        self.group_studies()
        self.save()
        return list({row["id"]: row for row in added}.values())

    def group_studies(self):
        """Union explicit work identifiers and exact titles, never author overlap alone."""
        sources = self.data["sources"]
        parent = {sid: sid for sid in sources}

        def find(sid):
            while parent[sid] != sid:
                sid = parent[sid]
            return sid

        seen = {}
        for sid, source in sources.items():
            for key in study_keys(source):
                if key in seen:
                    left, right = sorted((find(sid), find(seen[key])))
                    parent[right] = left
                seen[key] = sid
        for sid, source in sources.items():
            source["study_id"] = find(sid)
            source["study_group_basis"] = (
                "Explicit DOI/arXiv/version relation or exact normalized title; not author independence"
            )

    def document(self, source_id, paragraphs, access, url, content_hash):
        if source_id not in self.data["sources"]:
            raise ValueError("Unknown source; search before reading.")
        passages = []
        for index, row in enumerate(paragraphs):
            text = normalize(row["text"])
            if text:
                passages.append(
                    {
                        "id": f"P{index + 1:04}",
                        "text": text,
                        "locator": row.get("locator", "body"),
                    }
                )
        doc = {
            "source_id": source_id,
            "access": access,
            "url": url,
            "sha256": content_hash,
            "passages": passages,
        }
        self.data["documents"][source_id] = doc
        self.data.setdefault("document_versions", {})[
            source_id + ":" + content_hash
        ] = doc
        self.save()
        return doc

    def passages(self, source_id, passage_ids, document_sha256=None, neighbor_window=0):
        doc = (
            self.data.get("document_versions", {}).get(
                source_id + ":" + document_sha256
            )
            if document_sha256
            else self.data["documents"].get(source_id)
        )
        if not doc:
            raise ValueError("Read the source before requesting passages.")
        selected = [p for p in doc["passages"] if p["id"] in passage_ids]
        if len(selected) != len(set(passage_ids)):
            raise ValueError("Passage ID is not in this saved document.")
        if not isinstance(neighbor_window, int) or not 0 <= neighbor_window <= 2:
            raise ValueError("Neighbor window must be 0, 1 or 2")
        indices = {i for i, p in enumerate(doc["passages"]) if p["id"] in passage_ids}
        neighbors = {
            j
            for i in indices
            for j in range(
                max(0, i - neighbor_window),
                min(len(doc["passages"]), i + neighbor_window + 1),
            )
        }
        return {
            **doc,
            "passages": [p for i, p in enumerate(doc["passages"]) if i in neighbors],
            "requested_passage_ids": passage_ids,
        }

    def register(self, source_id, passage_id, quote, question_id):
        doc = self.passages(source_id, [passage_id])
        if doc["access"] in ("metadata_only", "unread"):
            raise ValueError(
                "Metadata alone cannot be registered as substantive evidence."
            )
        quote = normalize(quote)
        passage = doc["passages"][0]
        if not 25 <= len(quote) <= 1800 or quote not in passage["text"]:
            raise ValueError(
                "Quote must be an exact 25–1800 character substring of the saved passage."
            )
        start = passage["text"].index(quote)
        key = stable_id("E", source_id + doc["sha256"] + passage_id + quote)
        evidence = {
            "id": key,
            "source_id": source_id,
            "passage_id": passage_id,
            "quote": quote,
            "question_id": question_id,
            "start": start,
            "end": start + len(quote),
            "locator": passage["locator"],
            "document_sha256": doc["sha256"],
            "access": doc["access"],
            "url": doc["url"],
            "title": self.data["sources"][source_id]["title"],
        }
        self.data["evidence"][key] = evidence
        self.save()
        return evidence
