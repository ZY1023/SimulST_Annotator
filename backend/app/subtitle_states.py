"""Reversible manual operations on OCR screen states; raw observations are untouched."""
import json
from contextlib import closing
from pathlib import Path
from uuid import uuid4

from .db import connect
from .ocr import normalize_text, now


def as_dict(row):
    return {**dict(row), "observation_ids": json.loads(row["observation_ids_json"]),
            "provenance": json.loads(row["provenance_json"])}


def selected(db, media_id: str, state_ids: list[str]):
    if len(state_ids) < 1 or len(state_ids) != len(set(state_ids)):
        raise ValueError("Select distinct subtitle states")
    rows = db.execute(f"SELECT * FROM subtitle_screen_states WHERE media_id=? AND active=1 AND id IN ({','.join('?' for _ in state_ids)}) ORDER BY start_ms,id",
                      [media_id, *state_ids]).fetchall()
    if len(rows) != len(state_ids) or len({row["run_id"] for row in rows}) != 1:
        raise ValueError("Select active states from one OCR run")
    return rows


def insert_state(db_path: Path, media_id: str, run_id: str, start_ms: int, end_ms: int, text: str):
    if start_ms < 0 or end_ms <= start_ms: raise ValueError("Invalid subtitle interval")
    with closing(connect(db_path)) as db, db:
        run = db.execute("SELECT 1 FROM ocr_runs WHERE id=? AND media_id=?", (run_id, media_id)).fetchone()
        if not run: raise ValueError("OCR run not found")
        state_id = str(uuid4())
        db.execute("""INSERT INTO subtitle_screen_states
            (id,media_id,start_ms,end_ms,precision,text,observation_ids_json,provenance_json,review_status,run_id)
            VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (state_id, media_id, start_ms, end_ms, "estimated", text, "[]",
             json.dumps({"method": "manual_insert", "confidence": None, "needs_review": True}), "unreviewed", run_id))
        return as_dict(db.execute("SELECT * FROM subtitle_screen_states WHERE id=?", (state_id,)).fetchone())


def merge_preview(db_path: Path, media_id: str, state_ids: list[str], gap_tolerance_ms: int = 100):
    with closing(connect(db_path)) as db:
        rows = selected(db, media_id, state_ids)
    if len(rows) < 2: raise ValueError("Select at least two states")
    gaps = [rows[index + 1]["start_ms"] - rows[index]["end_ms"] for index in range(len(rows) - 1)]
    if any(gap > gap_tolerance_ms or gap < -gap_tolerance_ms for gap in gaps):
        raise ValueError("Selected states are separated by a meaningful gap or overlap")
    distinct = {normalize_text(row["text"]) for row in rows}
    if "" in distinct and len(distinct) > 1:
        raise ValueError("Blank disappearance cannot be merged with visible text")
    return {"state_ids": [row["id"] for row in rows], "start_ms": rows[0]["start_ms"],
            "end_ms": rows[-1]["end_ms"], "texts": [row["text"] for row in rows],
            "equivalent": len(distinct) == 1, "requires_confirmation": len(distinct) > 1,
            "recommended_action": "merge" if len(distinct) == 1 else "group_sequence",
            "gap_tolerance_ms": gap_tolerance_ms}


def merge_states(db_path: Path, media_id: str, state_ids: list[str], confirm_different: bool = False,
                 text: str | None = None):
    preview = merge_preview(db_path, media_id, state_ids)
    if preview["requires_confirmation"] and not confirm_different:
        raise ValueError("Different subtitle text requires explicit confirmation; consider grouping")
    if preview["requires_confirmation"] and text is None:
        raise ValueError("Choose the merged visible text explicitly")
    with closing(connect(db_path)) as db, db:
        rows = selected(db, media_id, state_ids)
        operation_id, result_id = str(uuid4()), str(uuid4())
        observation_ids = list(dict.fromkeys(oid for row in rows for oid in json.loads(row["observation_ids_json"])))
        merged_text = rows[0]["text"] if text is None else text
        db.execute("""INSERT INTO subtitle_screen_states
            (id,media_id,start_ms,end_ms,precision,text,observation_ids_json,provenance_json,review_status,run_id,operation_id,sequence_id)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (result_id, media_id, preview["start_ms"], preview["end_ms"], "estimated", merged_text,
             json.dumps(observation_ids), json.dumps({"method": "manual_merge", "source_state_ids": preview["state_ids"],
                                                      "confidence": None, "needs_review": True}), "unreviewed",
             rows[0]["run_id"], operation_id, rows[0]["sequence_id"]))
        db.executemany("UPDATE subtitle_screen_states SET active=0 WHERE id=?", [(row["id"],) for row in rows])
        db.execute("INSERT INTO subtitle_state_operations VALUES (?,?,?,?,?,?,?,?)",
                   (operation_id, media_id, rows[0]["run_id"], "merge", json.dumps(preview["state_ids"]),
                    json.dumps([result_id]), "applied", now()))
        return {"operation_id": operation_id, "state": as_dict(db.execute("SELECT * FROM subtitle_screen_states WHERE id=?", (result_id,)).fetchone())}


def split_state(db_path: Path, media_id: str, state_id: str, split_ms: int):
    with closing(connect(db_path)) as db, db:
        row = selected(db, media_id, [state_id])[0]
        if split_ms <= row["start_ms"] or split_ms >= row["end_ms"]:
            raise ValueError("Split point must be inside the state")
        operation_id, ids = str(uuid4()), [str(uuid4()), str(uuid4())]
        for new_id, start, end in ((ids[0], row["start_ms"], split_ms), (ids[1], split_ms, row["end_ms"])):
            db.execute("""INSERT INTO subtitle_screen_states
                (id,media_id,start_ms,end_ms,precision,text,observation_ids_json,provenance_json,review_status,run_id,operation_id,sequence_id)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (new_id, media_id, start, end, "estimated", row["text"], row["observation_ids_json"],
                 json.dumps({"method": "manual_split", "source_state_id": state_id,
                             "confidence": None, "needs_review": True}), "unreviewed", row["run_id"],
                 operation_id, row["sequence_id"]))
        db.execute("UPDATE subtitle_screen_states SET active=0 WHERE id=?", (state_id,))
        db.execute("INSERT INTO subtitle_state_operations VALUES (?,?,?,?,?,?,?,?)",
                   (operation_id, media_id, row["run_id"], "split", json.dumps([state_id]), json.dumps(ids), "applied", now()))
        return {"operation_id": operation_id, "states": [as_dict(db.execute("SELECT * FROM subtitle_screen_states WHERE id=?", (sid,)).fetchone()) for sid in ids]}


def toggle_operation(db_path: Path, media_id: str, operation_id: str, redo: bool = False):
    with closing(connect(db_path)) as db, db:
        row = db.execute("SELECT * FROM subtitle_state_operations WHERE id=? AND media_id=?", (operation_id, media_id)).fetchone()
        if not row: raise ValueError("Subtitle operation not found")
        expected = "undone" if redo else "applied"
        if row["status"] != expected: raise ValueError(f"Operation is not {expected}")
        source_ids, result_ids = json.loads(row["source_ids_json"]), json.loads(row["result_ids_json"])
        for sid in source_ids: db.execute("UPDATE subtitle_screen_states SET active=? WHERE id=?", (0 if redo else 1, sid))
        for sid in result_ids: db.execute("UPDATE subtitle_screen_states SET active=? WHERE id=?", (1 if redo else 0, sid))
        db.execute("UPDATE subtitle_state_operations SET status=? WHERE id=?", ("applied" if redo else "undone", operation_id))
        return {"operation_id": operation_id, "status": "applied" if redo else "undone"}


def group_states(db_path: Path, media_id: str, state_ids: list[str], sequence_id: str | None = None):
    with closing(connect(db_path)) as db, db:
        rows = selected(db, media_id, state_ids)
        if len(rows) < 2 and sequence_id is None: raise ValueError("Select at least two states to group")
        group_id = sequence_id or str(uuid4())
        db.executemany("UPDATE subtitle_screen_states SET sequence_id=? WHERE id=?", [(group_id, row["id"]) for row in rows])
        return {"sequence_id": group_id, "state_ids": [row["id"] for row in rows]}
