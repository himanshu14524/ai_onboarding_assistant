"""RAG answering, Response Review Agent, Learning Plan Agent (tool calling),
Readiness Pulse quiz, session memory and human escalation."""
import datetime as dt
import json
import random
import re
from collections import defaultdict

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool

from .config import HORIZON, MIN_RELEVANCE, PACE_TRAINING_MIN
from .db import ChatMessage, Employee, EmployeeTask, OpenQuestion, ReadinessResult, Task, emp_dict, task_dict
from .llm import get_llm, parse_json, text_of
from .rag import retrieve
from .tools import generate_checklist, lookup_training_schedule

STOP = set("the a an and or of to in on for is are be by with at as from that this it your you my i we do does how "
           "what when where who which can should will have has".split())


def toks(s: str) -> list:
    return [w for w in re.findall(r"[a-z0-9]+", s.lower()) if w not in STOP and len(w) > 2]


# ---------------------------------------------------------------- session memory
def employee_tasks(db, emp: Employee) -> list:
    rows = (db.query(EmployeeTask, Task).join(Task, Task.id == EmployeeTask.task_id)
            .filter(EmployeeTask.employee_id == emp.id).all())
    return [{**task_dict(t), "status": et.status} for et, t in rows]


def memory_snapshot(db, emp: Employee) -> dict:
    tasks = employee_tasks(db, emp)
    open_q = db.query(OpenQuestion).filter_by(employee_id=emp.id, status="open").all()
    return {
        **emp_dict(emp),
        "completed_tasks": [t["title"] for t in tasks if t["status"] == "done"],
        "delayed_tasks": [t["title"] for t in tasks if t["status"] == "delayed"],
        "open_questions": [q.question for q in open_q],
        "weak_topics": weak_topics(db, emp),
    }


# ---------------------------------------------------------------- RAG answer
ANSWER_SYS = """You are an onboarding assistant. Answer ONLY from the numbered context.
Cite every claim like [S1]. If the context does not contain the answer, reply exactly: INSUFFICIENT_EVIDENCE.
Tailor the answer to the employee's role, location and learning pace. Be concise (max ~120 words)."""

REVIEW_SYS = """You are the Response Review Agent. Check that every claim in the draft answer is supported by the
numbered evidence and cited. Return ONLY JSON:
{"supported": bool, "confidence": number 0-1, "unsupported_claims": [str],
 "verdict": "approve"|"revise"|"reject", "revised_answer": str|null}
Use "revise" when small fixes suffice (put the corrected answer, using only supported claims, in revised_answer).
Use "reject" when the evidence does not answer the question."""


def _ctx(hits):
    return "\n\n".join(f"[S{i + 1}] ({h['source']} - {h['section']})\n{h['text']}" for i, h in enumerate(hits))


def extractive_answer(question: str, hits: list) -> str:
    q, scored = set(toks(question)), []
    for i, h in enumerate(hits):
        for s in re.split(r"(?<=[.!?])\s+|\n+", h["text"]):
            s = s.strip(" -*\u2022#")
            if len(s) < 20:
                continue
            ov = len(q & set(toks(s)))
            if ov:
                scored.append((ov + h["score"], i, s))
    scored.sort(reverse=True)
    if not scored:
        return "INSUFFICIENT_EVIDENCE"
    return " ".join(f"{s} [S{i + 1}]" for _, i, s in scored[:3])


def draft_answer(question, mem, hits, history) -> str:
    llm = get_llm()
    if llm:
        try:
            msgs = [SystemMessage(content=ANSWER_SYS)]
            for m in history:  # conversation memory
                msgs.append(HumanMessage(content=m.content) if m.role == "user" else AIMessage(content=m.content))
            msgs.append(HumanMessage(content=f"Employee profile & session memory:\n{json.dumps(mem)}\n\n"
                                             f"Context:\n{_ctx(hits)}\n\nQuestion: {question}"))
            return text_of(llm.invoke(msgs)).strip()
        except Exception as exc:
            print(f"[answer] LLM failed: {exc}")
    return extractive_answer(question, hits)


def review_answer(question, answer, hits) -> dict:
    llm = get_llm()
    if llm:
        try:
            out = parse_json(text_of(llm.invoke([
                SystemMessage(content=REVIEW_SYS),
                HumanMessage(content=f"Question: {question}\n\nDraft answer:\n{answer}\n\nEvidence:\n{_ctx(hits)}")])))
            if isinstance(out, dict) and out.get("verdict") in ("approve", "revise", "reject"):
                out["confidence"] = float(out.get("confidence", 0.5))
                out["method"] = "llm"
                return out
        except Exception as exc:
            print(f"[review] LLM failed: {exc}")
    ev = set(toks(" ".join(h["text"] for h in hits)))
    ans = toks(re.sub(r"\[S\d+\]", "", answer))
    support = sum(1 for w in ans if w in ev) / max(1, len(ans))
    conf = round(0.6 * support + 0.4 * min(1.0, hits[0]["score"] / 0.6), 2)
    verdict = "approve" if conf >= 0.5 else "revise" if conf >= 0.35 else "reject"
    return {"supported": support > 0.6, "confidence": conf, "unsupported_claims": [],
            "verdict": verdict, "revised_answer": None, "method": "rules"}


def _escalate(db, emp, question, hits, reason) -> dict:
    """Human Escalation: concise question + retrieved evidence for HR."""
    evidence = [{"source": h["source"], "section": h["section"], "text": h["text"][:300], "score": h["score"]}
                for h in hits[:3]]
    q = OpenQuestion(employee_id=emp.id, evidence=evidence,
                     question=f"[{emp.role} | {emp.location}] {question.strip()} ({reason})")
    db.add(q)
    db.commit()
    return {"id": q.id, "question": q.question}


def answer_question(db, emp: Employee, question: str) -> dict:
    history = (db.query(ChatMessage).filter_by(employee_id=emp.id)
               .order_by(ChatMessage.id.desc()).limit(6).all())[::-1]
    mem = memory_snapshot(db, emp)
    hits = [h for h in retrieve(question) if h["score"] >= MIN_RELEVANCE]
    escalation, review = None, {"verdict": "reject", "confidence": 0.0, "method": "none"}

    if not hits:
        answer = ("I couldn't find this in the onboarding documents, so I've prepared a question for HR "
                  "to answer for you.")
        escalation = _escalate(db, emp, question, retrieve(question, k=3), "no relevant policy found")
    else:
        answer = draft_answer(question, mem, hits, history)
        if "INSUFFICIENT_EVIDENCE" in answer:
            review = {"verdict": "reject", "confidence": 0.0, "method": "answer-model"}
        else:
            review = review_answer(question, answer, hits)
            if review["verdict"] == "revise" and review.get("revised_answer"):
                answer = review["revised_answer"]
        if review["verdict"] == "reject" or review["confidence"] < 0.35:
            escalation = _escalate(db, emp, question, hits, "answer not sufficiently supported by policy")
            answer = ("The documents don't clearly answer this, so I've sent a concise question with the closest "
                      "evidence to HR. Closest matches are listed below.")

    cited = {int(n) for n in re.findall(r"\[S(\d+)\]", answer)}
    sources = [{"label": f"S{i + 1}", "cited": (i + 1) in cited, **h} for i, h in enumerate(hits)]
    review = {**review, "escalated": escalation is not None}
    db.add(ChatMessage(employee_id=emp.id, role="user", content=question))
    db.add(ChatMessage(employee_id=emp.id, role="assistant", content=answer, sources=sources, review=review))
    db.commit()
    return {"answer": answer, "sources": sources, "review": review, "escalation": escalation}


# ---------------------------------------------------------------- Learning Plan Agent
def workday(joining_iso, n):
    try:
        d = dt.date.fromisoformat(joining_iso)
    except Exception:
        return None
    while d.weekday() >= 5:
        d += dt.timedelta(days=1)
    c = 1
    while c < n:
        d += dt.timedelta(days=1)
        if d.weekday() < 5:
            c += 1
    return d.isoformat()


def _int(v):
    try:
        return int(str(v).strip())
    except Exception:
        return 0


def _checklist_for(db, emp):
    tasks = employee_tasks(db, emp)
    return generate_checklist(tasks, [t["id"] for t in tasks if t["status"] == "done"],
                              [t["id"] for t in tasks if t["status"] == "delayed"], emp.pace)


def _make_tools(db, emp):
    @tool
    def training_schedule_lookup(role: str, location: str = "") -> str:
        """Look up training sessions from the training schedule CSV for a role and location."""
        return json.dumps(lookup_training_schedule(role, location))

    @tool
    def checklist_generator() -> str:
        """Generate the adaptive first-week checklist for the current employee (tasks per day, blocked, at-risk)."""
        cl = _checklist_for(db, emp)
        return json.dumps({"days": {d: [t["title"] for t in ts] for d, ts in cl["days"].items()},
                           "blocked": [f"{t['title']} ({t['reason']})" for t in cl["blocked"]],
                           "unscheduled": [t["title"] for t in cl["unscheduled"]]})

    return [training_schedule_lookup, checklist_generator]


PLAN_SYS = """You are the Learning Plan Agent for new-hire onboarding. Call the tools to fetch the training schedule
and the adaptive checklist for this employee, then reply with ONLY JSON:
{"coach_note": "3 friendly sentences tailored to role, location and pace", "priorities": ["top 3 things to do first"]}
Do not invent tasks or sessions that the tools did not return."""


def _agent_note(db, emp, plan_stub: dict) -> dict:
    llm = get_llm()
    default = {"coach_note": f"Welcome, {emp.name}! Your first week is organised day by day at a "
                             f"{emp.pace.lower()} pace. Finish the mandatory tasks first, then use the training links.",
               "priorities": [t["title"] for t in plan_stub["days"][0]["tasks"][:3]]}
    if not llm:
        return default
    try:
        tools = _make_tools(db, emp)
        tmap = {t.name: t for t in tools}
        bound = llm.bind_tools(tools)
        msgs = [SystemMessage(content=PLAN_SYS),
                HumanMessage(content=f"Employee: {json.dumps(emp_dict(emp))}. "
                                     f"Already completed: {memory_snapshot(db, emp)['completed_tasks']}")]
        for _ in range(4):  # manual tool-calling loop
            ai = bound.invoke(msgs)
            msgs.append(ai)
            if not getattr(ai, "tool_calls", None):
                break
            for tc in ai.tool_calls:
                out = tmap[tc["name"]].invoke(tc["args"])
                msgs.append(ToolMessage(content=str(out), tool_call_id=tc["id"]))
        data = parse_json(text_of(ai))
        if isinstance(data, dict) and data.get("coach_note"):
            return {"coach_note": data["coach_note"], "priorities": data.get("priorities") or default["priorities"]}
    except Exception as exc:
        print(f"[plan-agent] LLM failed: {exc}")
    return default


def build_plan(db, emp: Employee) -> dict:
    cl = _checklist_for(db, emp)
    sessions = lookup_training_schedule(emp.role, emp.location)
    weak = weak_topics(db, emp)
    limit = PACE_TRAINING_MIN.get(emp.pace, 240)
    days = []
    for d in range(1, HORIZON + 1):
        ts = cl["days"][d]
        ss = [s for s in sessions if _int(s.get("day")) == d]
        mins = sum(_int(s.get("duration_min")) for s in ss)
        cps, warns = [], []
        mand = [t["title"] for t in ts if t.get("mandatory")]
        if mand:
            cps.append("Confirm completed: " + "; ".join(mand[:3]) + ("..." if len(mand) > 3 else ""))
        if d == 3:
            cps.append("Readiness Pulse: take the short knowledge check")
        if d == 5:
            cps.append("Readiness Pulse + 30-minute review with your manager")
        if weak and d >= 2:
            cps.append(f"Reinforce: {weak[(d - 2) % len(weak)]}")
        if mins > limit:
            warns.append(f"{mins} min of training exceeds your {emp.pace.lower()}-pace target of {limit} min")
        if any(t.get("at_risk") for t in ts):
            warns.append("Contains at-risk task(s) affected by a delay")
        days.append({"day": d, "date": workday(emp.joining_date, d), "tasks": ts, "training": ss,
                     "training_minutes": mins, "checkpoints": cps, "warnings": warns})
    plan = {"employee": emp_dict(emp), "days": days, "blocked": cl["blocked"],
            "unscheduled": cl["unscheduled"], "reinforcement": weak,
            "generated_at": dt.datetime.utcnow().isoformat(timespec="seconds")}
    plan.update(_agent_note(db, emp, plan))
    return plan


# ---------------------------------------------------------------- Readiness Pulse
def weak_topics(db, emp: Employee) -> list:
    agg = defaultdict(lambda: [0, 0])
    for r in db.query(ReadinessResult).filter_by(employee_id=emp.id).all():
        agg[r.topic][0] += r.correct
        agg[r.topic][1] += r.total
    return [t for t, (c, n) in agg.items() if n and c / n < 0.6]


def make_quiz(db, emp: Employee, n: int = 5) -> list:
    owners = sorted({t.owner for t in db.query(Task).all() if t.owner})
    pool = [t for t in employee_tasks(db, emp) if t.get("owner") or t.get("deadline_day")]
    rnd = random.Random()
    rnd.shuffle(pool)
    qs = []
    for t in pool:
        if len(qs) >= n:
            break
        if t.get("owner") and (not t.get("deadline_day") or rnd.random() < 0.5):
            wrong = [o for o in owners if o != t["owner"]]
            rnd.shuffle(wrong)
            opts, ans = [t["owner"]] + wrong[:3], t["owner"]
            if len(opts) < 2:
                continue
            q = f"Who is responsible for: \u201c{t['title']}\u201d?"
        else:
            ans, opts = f"Day {t['deadline_day']}", [f"Day {d}" for d in range(1, 6)]
            if ans not in opts:
                opts[0] = ans
            q = f"By which working day should \u201c{t['title']}\u201d be completed?"
        rnd.shuffle(opts)
        qs.append({"topic": t["category"] or "General", "question": q, "options": opts,
                   "answer": ans, "source": t["source_doc"]})
    return qs
