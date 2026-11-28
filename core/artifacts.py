"""Atomically merge branch documents by replaying exact evidence registration."""

from copy import deepcopy

from .store import Store


def merge_snapshot(store, snapshot):
    staged = object.__new__(Store)
    staged.data = deepcopy(store.data)
    staged.save = lambda: None
    source_map, evidence_map = {}, {}
    for sid, source in snapshot["sources"].items():
        source_map[sid] = staged.add_sources([source])[0]["id"]
    for evidence in snapshot["evidence"].values():
        old_sid, version = evidence["source_id"], evidence["document_sha256"]
        document = snapshot["document_versions"].get(old_sid + ":" + version)
        if document is None or document["sha256"] != version:
            raise ValueError("Branch evidence has no matching document version")
        sid = source_map[old_sid]
        staged.document(
            sid, document["passages"], document["access"], document["url"], version
        )
        registered = staged.register(
            sid, evidence["passage_id"], evidence["quote"], evidence["question_id"]
        )
        if (registered["start"], registered["end"]) != (
            evidence["start"],
            evidence["end"],
        ):
            raise ValueError("Branch evidence offsets do not match")
        evidence_map[evidence["id"]] = registered["id"]
    store.data = staged.data
    store.save()
    return {
        "source_map": source_map,
        "evidence_map": evidence_map,
        "sources": store.data["sources"],
        "evidence": store.data["evidence"],
    }
