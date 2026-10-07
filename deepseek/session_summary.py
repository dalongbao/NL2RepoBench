"""Summarize communication from the pinned harness's uncompressed session logs."""

import json
import re
from pathlib import Path


def _session_paths(root: Path) -> list:
    latest = {}
    for path in root.rglob("session*.jsonl"):
        match = re.fullmatch(r"session(?:\.v(\d+))?\.jsonl", path.name)
        if match:
            version = int(match.group(1) or 0)
            if path.parent not in latest or version > latest[path.parent][0]:
                latest[path.parent] = (version, path)
    return [path for _, path in latest.values()]


def summarize_sessions(root: Path, expected_teammates: int) -> dict:
    """Reject incomplete teams; CLI stdout alone omits teammate events."""
    sessions, members, tasks, queued, delivered = {}, {}, {}, {}, set()
    errors = []
    for path in _session_paths(root):
        try:
            with path.open(encoding="utf-8") as stream:
                header = json.loads(next(stream))
                if header.get("type") != "session" or header.get("version") != 4:
                    raise ValueError("Expected the pinned harness's session format v4")
                session_id = header["id"]
                session = {"id": session_id, "parent": header.get("parentSession"),
                           "path": str(path), "finish_reason": None, "open_turn": False}
                sessions[session_id] = session
                inboxes = {"next-step": [], "next-turn": []}
                for line in stream:
                    event = json.loads(line)
                    kind, data = event["type"], event.get("data", {})
                    if kind == "turn/start":
                        session["open_turn"] = True
                    elif kind == "turn/end":
                        session["open_turn"] = False
                        session["finish_reason"] = data["reason"]["kind"]
                    elif kind == "agent/inbox/spliced":
                        inbox = inboxes[data["target"]]
                        start = data["start"]
                        inbox[start:start + data.get("removedCount", 0)] = data.get("inserted", [])
                    # Forked sessions can inherit the lead's earlier Team records.
                    if header.get("parentSession") is not None:
                        continue
                    if kind.startswith("team/"):
                        if data.get("version") != 2 or data.get("teamId") != session_id:
                            raise ValueError("Unsupported or inconsistent Team event")
                    if kind == "team/member":
                        member = data["member"]
                        members[member["id"]] = member
                    elif kind == "team/task":
                        task = data["task"]
                        tasks[task["id"]] = task
                    elif kind == "team/message/queued":
                        message = data["message"]
                        queued[message["id"]] = {
                            "sender": message["senderId"], "target": message["targetId"],
                        }
                    elif kind == "team/message/delivered":
                        delivered.add(data["messageId"])
                session["pending_messages"] = sum(len(items) for items in inboxes.values())
        except (OSError, ValueError, KeyError, TypeError, StopIteration) as exc:
            errors.append(f"Cannot summarize {path}: {exc}")

    leads = [item["id"] for item in sessions.values() if item["parent"] is None]
    lead = leads[0] if len(leads) == 1 else None
    if lead is None:
        errors.append("Expected exactly one lead session")
    active = {key for key, member in members.items() if member["phase"] == "active"}
    if len(active) != expected_teammates or len(members) != expected_teammates:
        errors.append(f"Expected {expected_teammates} successfully created teammates; found {len(active)}")
    edges = {(queued[key]["sender"], queued[key]["target"]) for key in delivered if key in queued}
    if queued.keys() - delivered:
        errors.append("Team messages remained undelivered")
    for member_id in active:
        name = members[member_id]["name"]
        if (lead, member_id) not in edges or (member_id, lead) not in edges:
            errors.append(f"Missing delivered lead/teammate exchange for {name}")
    peer_messages = sum(sender in active and target in active and sender != target for sender, target in edges)
    if not peer_messages:
        errors.append("No delivered teammate-to-teammate message")
    if not tasks or any(task["status"] not in {"completed", "deleted"} for task in tasks.values()):
        errors.append("Shared task board is empty or has unfinished tasks")
    for session_id in {lead, *active} - {None}:
        session = sessions.get(session_id)
        if session is None or session["open_turn"] or session["finish_reason"] != "completed" or session.get("pending_messages", 0):
            errors.append(f"Session {session_id} did not finish all queued work successfully")
    return {
        "status": "completed" if not errors else "incomplete", "errors": errors,
        "lead_session_id": lead, "members": list(members.values()), "tasks": list(tasks.values()),
        "queued_message_count": len(queued), "delivered_message_count": len(delivered),
        "peer_communication_pairs": peer_messages, "sessions": list(sessions.values()),
    }
