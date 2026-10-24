import shutil
from datetime import datetime
from pathlib import Path
from typing import List, Literal, Optional

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy.orm import Session

from . import agents, rag
from .auth import (DEFAULT_HR_EMAIL, DEFAULT_HR_PASSWORD, current_user, hash_password, new_token,
                   require_hr, require_self_or_hr, verify_password)
from .config import DOC_TYPES, SAMPLES_DIR, TRAINING_CSV, UPLOAD_DIR
from .db import (AuthToken, ChatMessage, Document, Employee, EmployeeTask, OpenQuestion, ReadinessResult,
                 SessionLocal, Task, User, emp_dict, get_db, init_db, task_dict, user_dict)
from .extraction import extract_tasks, resolve_dependencies, save_tasks
from .llm import llm_name
from .tools import generate_checklist, lookup_training_schedule

app = FastAPI(title="AI Employee Onboarding Guide API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.on_event("startup")
def _startup():
    init_db()
    with SessionLocal() as db:  # seed a default HR account on first run
        if not db.query(User).filter_by(role="hr").first():
            db.add(User(email=DEFAULT_HR_EMAIL, password_hash=hash_password(DEFAULT_HR_PASSWORD), role="hr"))
            db.commit()
            print(f"[auth] created default HR login -> {DEFAULT_HR_EMAIL} / {DEFAULT_HR_PASSWORD} (change this!)")


# ------------------------------------------------------------------ helpers
def get_emp(db: Session, eid: int) -> Employee:
    e = db.get(Employee, eid)
    if not e:
        raise HTTPException(404, "Employee not found")
    return e


def sync_employee_tasks(db: Session, emp: Optional[Employee] = None):
    """Assign every applicable extracted task (all-role + this role) to the employee."""
    tasks = db.query(Task).all()
    for e in ([emp] if emp else db.query(Employee).all()):
        have = {et.task_id for et in db.query(EmployeeTask).filter_by(employee_id=e.id)}
        er = (e.role or "").lower()
        for t in tasks:
            if t.id in have:
                continue
            tr = (t.role or "").strip().lower()
            if tr and tr not in ("all", "everyone") and tr not in er and er not in tr:
                continue
            db.add(EmployeeTask(employee_id=e.id, task_id=t.id, status="todo"))
    db.commit()


def ingest_path(db: Session, path: Path, filename: str, doc_type: str) -> dict:
    if db.query(Document).filter_by(filename=filename).first():
        raise HTTPException(409, f"{filename} is already ingested")
    if doc_type == "training_schedule" and path.suffix.lower() == ".csv":
        head = path.read_text(encoding="utf-8-sig").splitlines()[0].lower()
        if not all(c in head for c in ("title", "role", "day", "link")):
            raise HTTPException(400, "Training CSV needs columns: title, role, day, link (see data/training_schedule.csv)")
        if path.resolve() != TRAINING_CSV.resolve():
            shutil.copy(path, TRAINING_CSV)
    n_chunks, full_text = rag.add_document(path, filename, doc_type)
    found, method = 0, ""
    if doc_type not in ("faq", "training_schedule"):
        items, method = extract_tasks(full_text)
        found = save_tasks(db, items, filename, method)
        resolve_dependencies(db)
        sync_employee_tasks(db)
    doc = Document(filename=filename, doc_type=doc_type, chunks=n_chunks, tasks_found=found,
                   extraction_method=method)
    db.add(doc)
    db.commit()
    return {"filename": filename, "doc_type": doc_type, "chunks": n_chunks, "tasks_found": found,
            "extraction_method": method or "n/a"}


# ------------------------------------------------------------------ auth
class LoginIn(BaseModel):
    email: str
    password: str


@app.post("/auth/login")
def login(body: LoginIn, db: Session = Depends(get_db)):
    u = db.query(User).filter_by(email=body.email.strip().lower()).first()
    if not u or not verify_password(body.password, u.password_hash):
        raise HTTPException(401, "Invalid email or password")
    token = new_token()
    db.add(AuthToken(token=token, user_id=u.id))
    db.commit()
    return {"token": token, **user_dict(u)}


@app.post("/auth/logout")
def logout(user: User = Depends(current_user), db: Session = Depends(get_db)):
    db.query(AuthToken).filter_by(user_id=user.id).delete()
    db.commit()
    return {"logged_out": True}


@app.get("/auth/me")
def me(user: User = Depends(current_user)):
    return user_dict(user)


# ------------------------------------------------------------------ health / documents
@app.get("/health")
def health():
    return {"status": "ok", "llm": llm_name()}


@app.post("/documents/upload", dependencies=[Depends(require_hr)])
def upload(file: UploadFile = File(...), doc_type: str = Form("policy"), db: Session = Depends(get_db)):
    if doc_type not in DOC_TYPES:
        raise HTTPException(400, f"doc_type must be one of {DOC_TYPES}")
    dest = UPLOAD_DIR / Path(file.filename).name
    with open(dest, "wb") as f:
        shutil.copyfileobj(file.file, f)
    return ingest_path(db, dest, dest.name, doc_type)


def _guess_type(name: str) -> str:
    n = name.lower()
    return ("faq" if "faq" in n else "role_profile" if "role" in n else
            "training_schedule" if "training" in n else "org_reference" if "org" in n else "policy")


@app.post("/documents/load-samples", dependencies=[Depends(require_hr)])
def load_samples(db: Session = Depends(get_db)):
    results = []
    for p in sorted(SAMPLES_DIR.iterdir()):
        if p.is_file() and not db.query(Document).filter_by(filename=p.name).first():
            results.append(ingest_path(db, p, p.name, _guess_type(p.name)))
    return {"ingested": results}


@app.get("/documents", dependencies=[Depends(require_hr)])
def documents(db: Session = Depends(get_db)):
    return [dict(id=d.id, filename=d.filename, doc_type=d.doc_type, chunks=d.chunks, tasks_found=d.tasks_found,
                 extraction_method=d.extraction_method, uploaded_at=d.uploaded_at.isoformat())
            for d in db.query(Document).order_by(Document.id).all()]


@app.delete("/documents/{doc_id}", dependencies=[Depends(require_hr)])
def delete_document(doc_id: int, db: Session = Depends(get_db)):
    d = db.get(Document, doc_id)
    if not d:
        raise HTTPException(404, "Document not found")
    rag.remove_document(d.filename)
    ids = [t.id for t in db.query(Task).filter_by(source_doc=d.filename)]
    if ids:
        db.query(EmployeeTask).filter(EmployeeTask.task_id.in_(ids)).delete(synchronize_session=False)
        db.query(Task).filter(Task.id.in_(ids)).delete(synchronize_session=False)
    db.delete(d)
    db.commit()
    return {"deleted": d.filename}


@app.get("/tasks", dependencies=[Depends(require_hr)])
def all_tasks(db: Session = Depends(get_db)):
    return [task_dict(t) for t in db.query(Task).order_by(Task.deadline_day, Task.id).all()]


@app.get("/schedule")
def schedule(role: str = "", location: str = ""):
    return lookup_training_schedule(role, location)


# ------------------------------------------------------------------ employees (role-aware intake)
class EmployeeIn(BaseModel):
    name: str
    role: str
    location: str = ""
    joining_date: Optional[str] = None
    experience: str = ""
    pace: Literal["Slow", "Normal", "Fast"] = "Normal"
    email: str
    password: str


class EmployeeUpdate(BaseModel):
    name: Optional[str] = None
    role: Optional[str] = None
    location: Optional[str] = None
    joining_date: Optional[str] = None
    experience: Optional[str] = None
    pace: Optional[Literal["Slow", "Normal", "Fast"]] = None


@app.post("/employees", dependencies=[Depends(require_hr)])
def create_employee(body: EmployeeIn, db: Session = Depends(get_db)):
    email = body.email.strip().lower()
    if db.query(User).filter_by(email=email).first():
        raise HTTPException(409, "That email already has a login")
    fields = body.model_dump(exclude={"email", "password"})
    e = Employee(**fields)
    db.add(e)
    db.commit()
    db.add(User(email=email, password_hash=hash_password(body.password), role="employee", employee_id=e.id))
    db.commit()
    sync_employee_tasks(db, e)
    return emp_dict(e)


@app.get("/employees", dependencies=[Depends(require_hr)])
def list_employees(db: Session = Depends(get_db)):
    return [emp_dict(e) for e in db.query(Employee).order_by(Employee.id).all()]


@app.patch("/employees/{eid}", dependencies=[Depends(require_self_or_hr)])
def update_employee(eid: int, body: EmployeeUpdate, db: Session = Depends(get_db)):
    e = get_emp(db, eid)
    for k, v in body.model_dump(exclude_none=True).items():
        setattr(e, k, v)
    db.commit()
    sync_employee_tasks(db, e)
    return emp_dict(e)


@app.get("/employees/{eid}/memory", dependencies=[Depends(require_self_or_hr)])
def memory(eid: int, db: Session = Depends(get_db)):
    return agents.memory_snapshot(db, get_emp(db, eid))


# ------------------------------------------------------------------ checklist / evidence map
class StatusIn(BaseModel):
    status: Literal["todo", "done", "delayed"]


@app.get("/employees/{eid}/checklist", dependencies=[Depends(require_self_or_hr)])
def checklist(eid: int, db: Session = Depends(get_db)):
    emp = get_emp(db, eid)
    tasks = agents.employee_tasks(db, emp)
    done = [t for t in tasks if t["status"] == "done"]
    cl = generate_checklist(tasks, [t["id"] for t in done],
                            [t["id"] for t in tasks if t["status"] == "delayed"], emp.pace)
    return {**cl, "done": done, "total": len(tasks), "progress": len(done) / len(tasks) if tasks else 0}


@app.post("/employees/{eid}/tasks/{task_id}/status", dependencies=[Depends(require_self_or_hr)])
def set_status(eid: int, task_id: int, body: StatusIn, db: Session = Depends(get_db)):
    et = db.query(EmployeeTask).filter_by(employee_id=eid, task_id=task_id).first()
    if not et:
        raise HTTPException(404, "Task not assigned to employee")
    et.status, et.updated_at = body.status, datetime.utcnow()
    db.commit()
    return {"task_id": task_id, "status": et.status}


@app.get("/employees/{eid}/evidence-map", dependencies=[Depends(require_self_or_hr)])
def evidence_map(eid: int, db: Session = Depends(get_db)):
    """Policy Evidence Map: every recommended task -> the policy section/training record it came from."""
    return [{"task": t["title"], "owner": t["owner"], "deadline_day": t["deadline_day"],
             "source_doc": t["source_doc"], "evidence": t["source_snippet"]}
            for t in agents.employee_tasks(db, get_emp(db, eid))]


# ------------------------------------------------------------------ plan
@app.post("/employees/{eid}/plan", dependencies=[Depends(require_self_or_hr)])
def plan(eid: int, db: Session = Depends(get_db)):
    return agents.build_plan(db, get_emp(db, eid))


# ------------------------------------------------------------------ chat (RAG + review + escalation)
class ChatIn(BaseModel):
    question: str


@app.post("/employees/{eid}/chat", dependencies=[Depends(require_self_or_hr)])
def chat(eid: int, body: ChatIn, db: Session = Depends(get_db)):
    return agents.answer_question(db, get_emp(db, eid), body.question)


@app.get("/employees/{eid}/chat", dependencies=[Depends(require_self_or_hr)])
def chat_history(eid: int, db: Session = Depends(get_db)):
    get_emp(db, eid)
    return [dict(role=m.role, content=m.content, sources=m.sources or [], review=m.review or {})
            for m in db.query(ChatMessage).filter_by(employee_id=eid).order_by(ChatMessage.id).all()]


# ------------------------------------------------------------------ readiness pulse
class QuizAnswer(BaseModel):
    topic: str
    question: str
    choice: str
    answer: str


@app.get("/employees/{eid}/quiz", dependencies=[Depends(require_self_or_hr)])
def quiz(eid: int, db: Session = Depends(get_db)):
    return agents.make_quiz(db, get_emp(db, eid))


@app.post("/employees/{eid}/quiz/submit", dependencies=[Depends(require_self_or_hr)])
def quiz_submit(eid: int, answers: List[QuizAnswer], db: Session = Depends(get_db)):
    emp = get_emp(db, eid)
    per = {}
    for a in answers:
        c, n = per.get(a.topic, (0, 0))
        per[a.topic] = (c + int(a.choice == a.answer), n + 1)
    for topic, (c, n) in per.items():
        db.add(ReadinessResult(employee_id=eid, topic=topic, correct=c, total=n))
    db.commit()
    total_c = sum(c for c, _ in per.values())
    return {"score": total_c, "total": len(answers), "weak_topics": agents.weak_topics(db, emp)}


# ------------------------------------------------------------------ human escalation
class ResolveIn(BaseModel):
    resolution: str


@app.get("/questions", dependencies=[Depends(require_hr)])
def questions(status: str = "open", employee_id: Optional[int] = None, db: Session = Depends(get_db)):
    q = db.query(OpenQuestion).filter_by(status=status)
    if employee_id:
        q = q.filter_by(employee_id=employee_id)
    return [dict(id=x.id, employee_id=x.employee_id, question=x.question, evidence=x.evidence,
                 status=x.status, resolution=x.resolution, created_at=x.created_at.isoformat())
            for x in q.order_by(OpenQuestion.id.desc()).all()]


@app.post("/questions/{qid}/resolve", dependencies=[Depends(require_hr)])
def resolve(qid: int, body: ResolveIn, db: Session = Depends(get_db)):
    q = db.get(OpenQuestion, qid)
    if not q:
        raise HTTPException(404, "Question not found")
    q.status, q.resolution = "resolved", body.resolution
    db.commit()
    return {"id": qid, "status": "resolved"}
