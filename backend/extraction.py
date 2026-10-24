"""Extract mandatory tasks, owners, deadlines, dependencies and role-specific
requirements from documents. LLM-based when a key is set, rule-based otherwise."""
import difflib
import json
import re

from .db import Task
from .llm import get_llm, parse_json, text_of

KIND_RE = re.compile(r"^[\-\*\u2022\d\.\)\s]*\**(mandatory|required|recommended)\**\s*:\s*(.+)$", re.I)
PROSE_RE = re.compile(r"\b(must|is required to|are required to|mandatory)\b", re.I)
OWNER_RE = re.compile(r"(?:owner|owned by|responsible|contact)\s*[:\-]?\s*([A-Z][\w &/]{1,40})")


def parse_deadline(s):
    if not s:
        return None
    s = s.lower()
    if m := re.search(r"day\s*(\d+)", s):
        return int(m.group(1))
    if m := re.search(r"within\s*(\d+)\s*(day|week)", s):
        return int(m.group(1)) * (5 if m.group(2) == "week" else 1)
    if re.search(r"week\s*1|first week|end of (the )?week", s):
        return 5
    if m := re.search(r"(\d+)\s*(?:working\s*)?days?", s):
        return int(m.group(1))
    return None


def heuristic_extract(text: str) -> list:
    items, heading = [], "General"
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("#"):
            heading = line.lstrip("# ").strip() or heading
            continue
        m = KIND_RE.match(line)
        if m:
            kind, rest = m.group(1).lower(), m.group(2)
            parts = [p.strip() for p in re.split(r"\.\s+|\.$", rest) if p.strip()]
            if not parts:
                continue
            fields = {}
            for p in parts[1:]:
                if ":" in p:
                    k, v = p.split(":", 1)
                    fields[k.strip().lower()] = v.strip()
            items.append({
                "title": parts[0][:300], "owner": fields.get("owner"),
                "deadline_day": parse_deadline(fields.get("deadline")),
                "depends_on": [x.strip() for x in re.split(r";", fields.get("depends on", "")) if x.strip()],
                "role": fields.get("role") if (fields.get("role") or "").lower() not in ("", "all", "everyone") else None,
                "category": fields.get("category") or heading, "mandatory": kind != "recommended",
                "evidence": line[:400],
            })
        elif PROSE_RE.search(line) and len(line) <= 240 and not line.endswith("?"):
            dl = parse_deadline(line)
            if dl and dl <= 10:  # prose only counts as a task when it carries a short deadline
                owner = OWNER_RE.search(line)
                items.append({"title": re.sub(r"^[\-\*\u2022\d\.\)\s]+", "", line)[:200],
                              "owner": owner.group(1).strip() if owner else None, "deadline_day": dl,
                              "depends_on": [], "role": None, "category": heading,
                              "mandatory": True, "evidence": line[:400]})
    return items


LLM_PROMPT = """Extract onboarding tasks from the text. Return ONLY a JSON array. Each item:
{"title": str, "owner": str|null, "deadline_day": int|null (working day number, 1 = joining day),
 "depends_on": [titles of prerequisite tasks], "role": str|null (null when it applies to everyone),
 "category": str, "mandatory": bool, "evidence": short verbatim snippet}
Only include concrete actions an employee must complete or attend. No policies or explanations."""


def llm_extract(llm, text: str) -> list:
    windows, buf = [], ""
    for line in text.splitlines(keepends=True):
        if len(buf) + len(line) > 4000 and buf:
            windows.append(buf)
            buf = ""
        buf += line
    if buf:
        windows.append(buf)
    items = []
    for w in windows[:8]:
        data = parse_json(text_of(llm.invoke(f"{LLM_PROMPT}\n\nTEXT:\n{w}")))
        if isinstance(data, list):
            items += [d for d in data if isinstance(d, dict) and d.get("title")]
    return items


def extract_tasks(text: str):
    llm = get_llm()
    if llm:
        try:
            items = llm_extract(llm, text)
            if items:
                return items, "llm"
        except Exception as exc:
            print(f"[extract] LLM failed, using rules: {exc}")
    return heuristic_extract(text), "rules"


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", (s or "").lower()).strip()


def save_tasks(db, items: list, source_doc: str, method: str) -> int:
    existing = {_norm(t.title) for t in db.query(Task).all()}
    added = 0
    for it in items:
        title = (it.get("title") or "").strip()
        if not title or _norm(title) in existing:
            continue
        dep = it.get("depends_on") or []
        dep_txt = "; ".join(dep) if isinstance(dep, list) else str(dep)
        dl = it.get("deadline_day")
        db.add(Task(title=title[:300], owner=it.get("owner"), deadline_day=int(dl) if str(dl).isdigit() else None,
                    category=it.get("category") or "General", role=it.get("role") or None,
                    depends_on_text=dep_txt or None, depends_on=[], mandatory=bool(it.get("mandatory", True)),
                    source_doc=source_doc, source_snippet=(it.get("evidence") or "")[:500], extracted_by=method))
        existing.add(_norm(title))
        added += 1
    db.commit()
    return added


def resolve_dependencies(db):
    """Map free-text prerequisites to task ids (substring, then fuzzy match)."""
    tasks = db.query(Task).all()
    titles = {t.id: t.title.lower() for t in tasks}
    for t in tasks:
        if not t.depends_on_text:
            continue
        ids = []
        for piece in re.split(r";", t.depends_on_text):
            p = piece.strip().lower().rstrip(".")
            if not p:
                continue
            best = next((tid for tid, ti in titles.items() if tid != t.id and (p in ti or ti in p)), None)
            if best is None:
                pool = {tid: ti for tid, ti in titles.items() if tid != t.id}
                m = difflib.get_close_matches(p, list(pool.values()), n=1, cutoff=0.6)
                if m:
                    best = next(tid for tid, ti in pool.items() if ti == m[0])
            if best and best not in ids:
                ids.append(best)
        t.depends_on = ids
    db.commit()
