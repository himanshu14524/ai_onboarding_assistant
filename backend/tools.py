"""Plain-Python tools: CSV training lookup + adaptive checklist generator."""
import csv
from pathlib import Path

from .config import HORIZON, PACE_CAP, TRAINING_CSV


def _int(v, default=99):
    try:
        return int(str(v).strip())
    except Exception:
        return default


def lookup_training_schedule(role: str, location: str = "", path=TRAINING_CSV) -> list:
    """Training sessions from the CSV for a role/location (rows marked All/Remote match everyone)."""
    if not Path(path).exists():
        return []
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    role_l, loc_l = (role or "").lower(), (location or "").lower()
    out = []
    for r in rows:
        r = {k: (v or "").strip() for k, v in r.items() if k}
        rr, rl = r.get("role", "All").lower(), r.get("location", "All").lower()
        role_ok = rr in ("", "all") or rr in role_l or role_l in rr
        loc_ok = not loc_l or rl in ("", "all", "remote") or rl == loc_l or loc_l in rl
        if role_ok and loc_ok:
            out.append(r)
    return sorted(out, key=lambda r: (_int(r.get("day")), r.get("start_time", "")))


def generate_checklist(tasks: list, done_ids, delayed_ids, pace: str = "Normal",
                       horizon: int = HORIZON) -> dict:
    """Adaptive checklist.
    - tasks blocked by a delayed prerequisite are pulled out and flagged at-risk
    - independent tasks are re-ordered/pulled forward (bounded by pace)
    - tasks due on/before a day are always scheduled (may exceed the pace cap)
    """
    cap = PACE_CAP.get(pace, 3)
    done, delayed = set(done_ids), set(delayed_ids)
    by_id = {t["id"]: t for t in tasks}
    pending = [t for t in tasks if t["id"] not in done]

    def delayed_ancestor(tid, seen=None):
        seen = seen if seen is not None else set()
        if tid in seen:
            return None
        seen.add(tid)
        for d in by_id[tid].get("depends_on", []):
            if d in done or d not in by_id:
                continue
            if d in delayed:
                return d
            a = delayed_ancestor(d, seen)
            if a:
                return a
        return None

    blocked, ok = [], []
    for t in pending:
        if t["id"] in delayed:
            blocked.append({**t, "reason": "Marked as delayed", "at_risk": True})
            continue
        a = delayed_ancestor(t["id"])
        if a:
            blocked.append({**t, "reason": f"Waiting on delayed task: {by_id[a]['title']}", "at_risk": True})
        else:
            ok.append(t)

    remaining = sorted(ok, key=lambda t: (t.get("deadline_day") or horizon,
                                          0 if t.get("mandatory", True) else 1, t["id"]))
    days = {d: [] for d in range(1, horizon + 1)}
    placed = {}

    def ready(t, day):
        return all(d in done or d not in by_id or (d in placed and placed[d] <= day)
                   for d in t.get("depends_on", []))

    for day in range(1, horizon + 1):
        progress = True
        while progress:
            progress = False
            for t in remaining:
                if not ready(t, day):
                    continue
                due = (t.get("deadline_day") or horizon) <= day
                if due or len(days[day]) < cap:
                    late = bool(t.get("deadline_day")) and day > t["deadline_day"]
                    days[day].append({**t, "at_risk": late,
                                      "note": "Scheduled after its deadline (dependency delay)" if late else ""})
                    placed[t["id"]] = day
                    remaining.remove(t)
                    progress = True
                    break

    unscheduled = [{**t, "reason": "Cannot be scheduled this week (dependency chain)", "at_risk": True}
                   for t in remaining]
    return {"days": days, "blocked": blocked, "unscheduled": unscheduled}
