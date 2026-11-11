"""Visual subtitle changes and independent human review records."""
import hashlib
import json
from contextlib import closing
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path

from .db import connect
from .ocr import normalize_text


VISUAL_TYPES = {"appearance", "addition", "deletion", "replacement", "disappearance",
                "mixed", "layout_only", "uncertain"}
REVIEW_STATUSES = {"unreviewed", "confirmed", "rejected", "uncertain"}


def visual_diff(before: str, after: str) -> dict:
    chunks = []
    counts = {"added": 0, "deleted": 0, "replaced": 0}
    kinds = set()
    for kind, a, b, c, d in SequenceMatcher(None, before, after, autojunk=False).get_opcodes():
        old, new = before[a:b], after[c:d]
        chunks.append({"kind": kind, "before": old, "after": new})
        if kind == "insert": counts["added"] += len(new); kinds.add("addition")
        elif kind == "delete": counts["deleted"] += len(old); kinds.add("deletion")
        elif kind == "replace": counts["replaced"] += max(len(old), len(new)); kinds.add("replacement")
    if not before and after: event_type = "appearance"
    elif before and not after: event_type = "disappearance"
    elif normalize_text(before) == normalize_text(after): event_type = "layout_only"
    elif len(kinds) == 1: event_type = next(iter(kinds))
    else: event_type = "mixed"
    return {"visual_event_type": event_type, "change_counts": counts, "diff": chunks}


def event_for(previous: dict, current: dict, run_id: str | None) -> dict | None:
    if previous["text"] == current["text"]: return None
    visual = visual_diff(previous["text"], current["text"])
    gap = current["start_ms"] - previous["end_ms"]
    if abs(gap) > 250: visual["visual_event_type"] = "uncertain"
    source = [previous["id"], previous["start_ms"], previous["end_ms"], previous["text"],
              current["id"], current["start_ms"], current["end_ms"], current["text"]]
    fingerprint = hashlib.sha256(json.dumps(source, ensure_ascii=False).encode()).hexdigest()
    return {"id": f"{previous['id']}:{current['id']}:update", "media_id": current["media_id"],
            "run_id": run_id, "start_ms": current["start_ms"], "end_ms": current["start_ms"],
            "previous_state_id": previous["id"], "current_state_id": current["id"],
            "before_text": previous["text"], "after_text": current["text"],
            "time_precision": current.get("precision", "estimated"), "source_fingerprint": fingerprint,
            "transition_gap_ms": gap, **visual}


def list_events(db_path: Path, media_id: str, run_id: str | None) -> list[dict]:
    with closing(connect(db_path)) as db:
        states = [dict(row) for row in db.execute("""SELECT * FROM subtitle_screen_states
            WHERE media_id=? AND run_id IS ? AND active=1 AND review_status!='rejected'
            ORDER BY start_ms,id""", (media_id, run_id))]
        reviews = {row["id"]: dict(row) for row in db.execute("""SELECT * FROM target_event_reviews
            WHERE media_id=? AND run_id IS ?""", (media_id, run_id))}
    initial = ({**states[0], "id": "initial-blank", "text": "", "end_ms": states[0]["start_ms"]}
               if states and states[0]["text"] else None)
    events = ([event_for(initial, states[0], run_id)] if initial else [])
    events += [event for previous, current in zip(states, states[1:])
              if (event := event_for(previous, current, run_id))]
    present = set()
    for event in events:
        present.add(event["id"])
        review = reviews.get(event["id"])
        event.update({"human_event_type": review["human_event_type"] if review else None,
                      "review_status": review["review_status"] if review else "unreviewed",
                      "notes": review["notes"] if review else "",
                      "invalidated": bool(review and (review["invalidated"] or review["source_fingerprint"] != event["source_fingerprint"])),
                      "orphaned": False})
    for review in reviews.values():
        if review["id"] in present: continue
        snapshot = json.loads(review["snapshot_json"])
        if not snapshot: continue
        events.append({**snapshot, "human_event_type": review["human_event_type"],
                       "review_status": review["review_status"], "notes": review["notes"],
                       "invalidated": True, "orphaned": True})
    return sorted(events, key=lambda item: (item["start_ms"], item["id"]))


def save_review(db_path: Path, media_id: str, run_id: str | None, event_id: str,
                fingerprint: str, human_type: str | None, status: str, notes: str) -> dict:
    if human_type is not None and human_type not in VISUAL_TYPES: raise ValueError("Unknown visual event type")
    if status not in REVIEW_STATUSES: raise ValueError("Unknown review status")
    if len(notes) > 4000: raise ValueError("Notes are too long")
    event = next((item for item in list_events(db_path, media_id, run_id) if item["id"] == event_id and not item["orphaned"]), None)
    if not event: raise ValueError("Event is no longer present; inspect its source states")
    if fingerprint != event["source_fingerprint"]: raise ValueError("Event changed; reload before saving")
    snapshot = {key: value for key, value in event.items() if key not in
                ("human_event_type", "review_status", "notes", "invalidated", "orphaned")}
    with connect(db_path) as db, db:
        db.execute("""INSERT INTO target_event_reviews
            (id,media_id,run_id,previous_state_id,current_state_id,source_fingerprint,
             human_event_type,review_status,notes,snapshot_json,invalidated,updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,0,?) ON CONFLICT(id) DO UPDATE SET
            source_fingerprint=excluded.source_fingerprint,human_event_type=excluded.human_event_type,
            review_status=excluded.review_status,notes=excluded.notes,snapshot_json=excluded.snapshot_json,
            invalidated=0,updated_at=excluded.updated_at""",
            (event_id, media_id, run_id, event["previous_state_id"], event["current_state_id"],
             fingerprint, human_type, status, notes, json.dumps(snapshot, ensure_ascii=False),
             datetime.now(timezone.utc).isoformat()))
    return next(item for item in list_events(db_path, media_id, run_id) if item["id"] == event_id)


def invalidate_for_states(db_path: Path, media_id: str, state_ids: list[str]) -> None:
    if not state_ids: return
    with connect(db_path) as db, db:
        marks = ','.join('?' for _ in state_ids)
        db.execute(f"""UPDATE target_event_reviews SET invalidated=1 WHERE media_id=? AND
            (previous_state_id IN ({marks}) OR current_state_id IN ({marks}))""",
            [media_id, *state_ids, *state_ids])
