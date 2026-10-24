# AI Employee Onboarding Guide (v2 — HR & Employee Dashboards)

Streamlit (frontend) + FastAPI (backend) + SQLite (DB) + Chroma (local vector DB) + LangChain. Python only.

## What's new in v2
- **Login system** with two roles: **HR** and **Employee**. Passwords are hashed (PBKDF2), sessions use bearer tokens.
- **Separate dashboards**: HR sees company-wide metrics and admin tools; each employee sees only their own progress.
- **Backend-enforced access control** — an employee cannot view another employee's data even by editing the URL.
- **Light blue / green / yellow theme** across the app (`.streamlit/config.toml` + custom CSS).

## Run
```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt

uvicorn backend.main:app --reload --port 8000      # terminal 1 (run from project root)
streamlit run frontend/app.py                      # terminal 2
```
LLM settings (DeepSeek-V3) live in `.env` — copy `.env.example` if you need to reset it.
First backend run downloads the `all-MiniLM-L6-v2` embedding model (needs internet once) and prints a default HR login:
```
hr@company.com / hr123
```

## First-time walkthrough
1. Log in as **HR** (`hr@company.com` / `hr123`).
2. **📁 Documents & Tasks → Load sample data** to ingest the sample policy/FAQ/role/training files.
3. **🧑‍💼 Onboard Employee** → create a new hire, giving them a login email + temporary password.
4. Log out, log back in with that employee's email/password → they land on their own **🏠 Dashboard**.
5. Explore Checklist, Plan, Chat, Readiness Pulse as the employee; go back to HR for Escalations.

## Roles at a glance
| | HR | Employee |
|---|---|---|
| Pages | Dashboard, Onboard Employee, Documents & Tasks, Escalations | Dashboard, My Checklist, First-Week Plan, Ask the Guide, Readiness Pulse |
| Can do | Upload documents, create/edit any employee, resolve escalated questions, see everyone's stats | See and update only their own checklist/plan/chat/quiz |
| Login | Seeded automatically on first backend start | Created by HR on **Onboard Employee** (sets their email + temp password) |

## Requirement → implementation
| Requirement | Where |
|---|---|
| Upload policies, FAQs, role profiles, schedules, org files | HR → Documents & Tasks → `POST /documents/upload` (pdf/docx/md/txt/csv) → `backend/rag.py` |
| Extract tasks, owners, deadlines, dependencies, role requirements | `backend/extraction.py` (LLM JSON extraction, rule-based fallback), stored in SQLite `tasks` |
| RAG answers with supporting source | `agents.answer_question` – Chroma retrieval, `[S#]` citations, source panel in chat |
| Remember role, location, completed tasks, open questions, pace | SQLite (`employees`, `employee_tasks`, `open_questions`, `chat_messages`) → `agents.memory_snapshot` |
| Personalised first-week plan | `agents.build_plan` → Plan page (+ markdown download) |
| Role-Aware Intake | HR → Onboard Employee |
| Policy Evidence Map | every task keeps `source_doc` + snippet; `/employees/{id}/evidence-map` |
| Adaptive Checklist | `tools.generate_checklist` – mark a task *Delayed* and dependents are blocked/at-risk |
| Readiness Pulse | `agents.make_quiz` / Readiness page; weak topics feed plan checkpoints |
| Human Escalation | low-confidence/no-evidence → `open_questions`; resolved on HR → Escalations |
| Tools | `tools.lookup_training_schedule` (CSV), `tools.generate_checklist` (Python function) |
| Agent roles | **Learning Plan Agent** (tool-calling loop), **Response Review Agent** (`agents.review_answer`) |
| **Login / access control (new)** | `backend/auth.py` (hashing, tokens), `dependencies=[Depends(require_hr)]` / `require_self_or_hr` on every route |
| **HR vs Employee dashboards (new)** | `frontend/app.py` — `EMP_PAGES`/`HR_PAGES`, role-based routing |

## Security notes for real use
- Change the default HR password immediately (`hr@company.com` / `hr123`).
- Tokens don't expire in this build — add an expiry check in `auth.current_user` before production use.
- Serve over HTTPS; `.env` holds a live API key, so never commit it or share the zip publicly.

## CSV format for training schedule
`course_id,title,role,day,start_time,duration_min,mode,location,link,mandatory,prerequisite`

## Task line format the rule-based extractor understands
`- Mandatory: <task title>. Owner: X. Deadline: Day N. Depends on: <other task title>. Role: <role>.`
With an LLM key set, free-form documents are handled by the model instead.
