import os
from datetime import date

import pandas as pd
import requests
import streamlit as st

API = os.getenv("API_URL", "http://localhost:8000")
ROLES = ["Software Engineer", "Data Analyst", "QA Engineer", "Business Analyst", "Other"]
LOCATIONS = ["Chennai", "Pune", "Bengaluru", "Remote"]
DOC_TYPES = ["policy", "faq", "role_profile", "training_schedule", "org_reference"]

EMP_PAGES = ["🏠 Dashboard", "✅ My Checklist", "🗓️ First-Week Plan", "💬 Ask the Guide", "🧠 Readiness Pulse"]
HR_PAGES = ["🏠 HR Dashboard", "🧑‍💼 Onboard Employee", "📁 Documents & Tasks", "🚨 Escalations"]

st.set_page_config(page_title="AI Employee Onboarding Guide", page_icon="🧭", layout="wide")

# ---------------------------------------------------------------- theme (light blue / green / yellow)
st.markdown("""
<style>
:root {
    --sky: #eaf6fb;      /* light blue background */
    --mint: #e7f8ee;     /* light green accent */
    --sun: #fff8dd;      /* light yellow accent */
    --blue: #2f9bd6;
    --green: #34a870;
    --yellow: #e8b923;
}
.stApp { background: linear-gradient(180deg, var(--sky) 0%, #ffffff 260px); }
section[data-testid="stSidebar"] { background: #eef8f2; border-right: 1px solid #d7ecdf; }
h1, h2, h3 { color: #1c5d80 !important; }
div[data-testid="stMetric"] {
    background: var(--sun); border: 1px solid #f0e2a6; border-radius: 12px; padding: 10px 14px;
}
.stButton>button[kind="primary"] { background-color: var(--blue); border-color: var(--blue); }
.stButton>button[kind="primary"]:hover { background-color: #227fb3; border-color: #227fb3; }
div[data-testid="stExpander"] { border: 1px solid #d7ecdf; border-radius: 10px; }
.stProgress > div > div > div > div { background-color: var(--green); }
.badge-green { background: var(--mint); color: #1e7a4c; padding: 2px 10px; border-radius: 999px; font-size: 0.85em; }
.badge-yellow { background: var(--sun); color: #8a6d05; padding: 2px 10px; border-radius: 999px; font-size: 0.85em; }
.badge-blue { background: var(--sky); color: #1c5d80; padding: 2px 10px; border-radius: 999px; font-size: 0.85em; }
</style>
""", unsafe_allow_html=True)


# ------------------------------------------------------------------ API helper (auth-aware)
def api(method, path, **kw):
    headers = kw.pop("headers", {})
    if st.session_state.get("token"):
        headers["Authorization"] = f"Bearer {st.session_state['token']}"
    try:
        r = requests.request(method, f"{API}{path}", headers=headers, timeout=kw.pop("timeout", 180), **kw)
        if r.status_code == 401:
            st.session_state.clear()
            st.warning("Your session expired. Please log in again.")
            st.rerun()
        if r.status_code >= 400:
            st.error(f"{r.status_code}: {r.text}")
            return None
        return r.json()
    except requests.exceptions.RequestException as exc:
        st.error(f"Backend not reachable at {API}. Start it with `uvicorn backend.main:app --reload`.\n\n{exc}")
        return None


def need_employee():
    eid = st.session_state.get("emp_id")
    if not eid:
        st.info("No employee profile is linked to this login yet.")
        st.stop()
    return eid


# ------------------------------------------------------------------ login screen
def login_screen():
    st.markdown("<h1 style='text-align:center'>🧭 AI Employee Onboarding Guide</h1>", unsafe_allow_html=True)
    st.markdown("<p style='text-align:center;color:#4a4a4a'>Log in to continue</p>", unsafe_allow_html=True)
    _, mid, _ = st.columns([1, 1.2, 1])
    with mid:
        with st.form("login"):
            email = st.text_input("Email", placeholder="you@company.com")
            pw = st.text_input("Password", type="password")
            submitted = st.form_submit_button("Log in", type="primary", use_container_width=True)
        if submitted:
            try:
                r = requests.post(f"{API}/auth/login", json={"email": email, "password": pw}, timeout=30)
            except requests.exceptions.RequestException as exc:
                st.error(f"Backend not reachable at {API}.\n\n{exc}")
                st.stop()
            if r.status_code == 200:
                d = r.json()
                st.session_state.update(token=d["token"], role=d["role"], user_email=d["email"],
                                        emp_id=d.get("employee_id"))
                st.rerun()
            else:
                st.error("Invalid email or password.")
        st.caption("First time? HR logs in with the default account shown in the backend terminal on first "
                  "start (`hr@company.com` / `hr123`), then creates employee logins on **Onboard Employee**.")
    st.stop()


# ------------------------------------------------------------------ sidebar
def sidebar():
    st.sidebar.markdown("## 🧭 Onboarding Guide")
    health = api("GET", "/health")
    if health:
        st.sidebar.caption(f"LLM: {health['llm']}")
    st.sidebar.markdown(f"**{st.session_state['user_email']}**  \n"
                        f"<span class='badge-{'green' if st.session_state['role']=='hr' else 'blue'}'>"
                        f"{'HR' if st.session_state['role']=='hr' else 'Employee'}</span>", unsafe_allow_html=True)
    st.sidebar.divider()
    pages = HR_PAGES if st.session_state["role"] == "hr" else EMP_PAGES
    page = st.sidebar.radio("Go to", pages, label_visibility="collapsed")
    st.sidebar.divider()

    if st.session_state["role"] == "employee" and st.session_state.get("emp_id"):
        mem = api("GET", f"/employees/{st.session_state['emp_id']}/memory")
        if mem:
            with st.sidebar.expander("Session memory", expanded=True):
                st.write(f"**Role:** {mem['role']}  \n**Location:** {mem['location']}  \n**Pace:** {mem['pace']}")
                st.write(f"**Completed:** {len(mem['completed_tasks'])}  \n**Delayed:** {len(mem['delayed_tasks'])}  \n"
                         f"**Open questions:** {len(mem['open_questions'])}")
                if mem["weak_topics"]:
                    st.write("**Needs reinforcement:** " + ", ".join(mem["weak_topics"]))

    if st.sidebar.button("Log out", use_container_width=True):
        api("POST", "/auth/logout")
        st.session_state.clear()
        st.rerun()
    return page


# ------------------------------------------------------------------ EMPLOYEE: dashboard
def page_emp_dashboard():
    eid = need_employee()
    mem = api("GET", f"/employees/{eid}/memory") or {}
    st.header(f"Welcome, {mem.get('name', '')} 👋")
    data = api("GET", f"/employees/{eid}/checklist") or {}
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Tasks done", f"{len(data.get('done', []))}/{data.get('total', 0)}")
    c2.metric("Delayed", len(mem.get("delayed_tasks", [])))
    c3.metric("Open questions", len(mem.get("open_questions", [])))
    c4.metric("Pace", mem.get("pace", "-"))
    if data.get("total"):
        st.progress(data["progress"], text=f"{int(data['progress']*100)}% of your first-week checklist complete")
    if mem.get("weak_topics"):
        st.warning("📌 Topics to revisit: " + ", ".join(mem["weak_topics"]))
    st.divider()
    left, right = st.columns(2)
    with left:
        st.subheader("Next up")
        upcoming = [t for d in sorted(data.get("days", {}), key=int) for t in data["days"][d]][:5]
        if upcoming:
            for t in upcoming:
                st.markdown(f"- {'⚠️ ' if t['at_risk'] else ''}**{t['title']}** · owner: {t['owner'] or '-'}")
        else:
            st.caption("Nothing pending — nice work!")
    with right:
        st.subheader("Quick links")
        st.markdown("- Go to **✅ My Checklist** to mark tasks done or delayed\n"
                    "- Go to **🗓️ First-Week Plan** for your day-by-day guide with training links\n"
                    "- Go to **💬 Ask the Guide** for policy questions with sources\n"
                    "- Go to **🧠 Readiness Pulse** for a short knowledge check")


# ------------------------------------------------------------------ checklist
def page_checklist():
    eid = need_employee()
    st.header("Adaptive Checklist")
    data = api("GET", f"/employees/{eid}/checklist")
    if not data:
        return
    if not data["total"]:
        st.info("No tasks yet. Ask HR to ingest onboarding documents.")
        return
    st.progress(data["progress"], text=f"{len(data['done'])} of {data['total']} tasks complete")

    def set_status(tid, status):
        if api("POST", f"/employees/{eid}/tasks/{tid}/status", json={"status": status}):
            st.rerun()

    def evidence(t):
        with st.expander("Policy evidence"):
            st.caption(f"Source: {t['source_doc']} · extracted by {t['extracted_by']}")
            st.write(t["source_snippet"] or "-")

    for d, tasks in data["days"].items():
        if not tasks:
            continue
        with st.expander(f"Day {d} · {len(tasks)} task(s)", expanded=int(d) <= 2):
            for t in tasks:
                c1, c2, c3 = st.columns([6, 1.2, 1.2])
                flag = " ⚠️ at risk" if t["at_risk"] else ""
                c1.markdown(f"**{t['title']}**{flag}  \nOwner: {t['owner'] or '-'} · Due: Day {t['deadline_day'] or '-'}"
                            f"{' · Role: ' + t['role'] if t['role'] else ''}")
                if t["note"]:
                    c1.caption(t["note"])
                if c2.button("✅ Done", key=f"d{t['id']}"):
                    set_status(t["id"], "done")
                if c3.button("⏸ Delay", key=f"x{t['id']}"):
                    set_status(t["id"], "delayed")
                evidence(t)
    if data["blocked"] or data["unscheduled"]:
        st.subheader("Blocked / at risk")
        for t in data["blocked"] + data["unscheduled"]:
            c1, c2 = st.columns([7, 1.5])
            c1.markdown(f"🚧 **{t['title']}**  \n{t['reason']}")
            if t["status"] == "delayed" and c2.button("Resume", key=f"r{t['id']}"):
                set_status(t["id"], "todo")
    if data["done"]:
        with st.expander(f"Completed ({len(data['done'])})"):
            for t in data["done"]:
                c1, c2 = st.columns([7, 1.5])
                c1.write(f"✅ {t['title']}")
                if c2.button("Undo", key=f"u{t['id']}"):
                    set_status(t["id"], "todo")
    with st.expander("Policy Evidence Map (all tasks → source)"):
        ev = api("GET", f"/employees/{eid}/evidence-map")
        if ev:
            st.dataframe(pd.DataFrame(ev), use_container_width=True, hide_index=True)


# ------------------------------------------------------------------ plan
def plan_markdown(p):
    e = p["employee"]
    out = [f"# First-week plan · {e['name']} ({e['role']}, {e['location']})", "", p["coach_note"], ""]
    for d in p["days"]:
        out.append(f"## Day {d['day']} {('· ' + d['date']) if d['date'] else ''}")
        out += [f"- [ ] {t['title']} (owner: {t['owner'] or '-'}; source: {t['source_doc']})" for t in d["tasks"]]
        out += [f"- Training: [{s['title']}]({s['link']}) {s['start_time']} ({s['duration_min']} min)" for s in d["training"]]
        out += [f"- Checkpoint: {c}" for c in d["checkpoints"]] + [f"- ⚠️ {w}" for w in d["warnings"]] + [""]
    return "\n".join(out)


def page_plan():
    eid = need_employee()
    st.header("Personalised First-Week Plan")
    if st.button("Generate / refresh plan", type="primary"):
        with st.spinner("Learning Plan Agent is working…"):
            st.session_state["plan"] = api("POST", f"/employees/{eid}/plan")
    p = st.session_state.get("plan")
    if not p or p["employee"]["id"] != eid:
        st.info("Click the button to build the plan from the latest checklist and training schedule.")
        return
    st.success(p["coach_note"])
    if p["priorities"]:
        st.markdown("**Start with:** " + " · ".join(p["priorities"]))
    if p["reinforcement"]:
        st.warning("Reinforcement needed: " + ", ".join(p["reinforcement"]))
    tabs = st.tabs([f"Day {d['day']}" for d in p["days"]])
    for tab, d in zip(tabs, p["days"]):
        with tab:
            if d["date"]:
                st.caption(d["date"])
            for w in d["warnings"]:
                st.warning(w)
            st.markdown("**Tasks**")
            for t in d["tasks"] or []:
                st.markdown(f"- {'⚠️ ' if t['at_risk'] else ''}{t['title']} — _{t['owner'] or 'n/a'}_ · source: `{t['source_doc']}`")
            if not d["tasks"]:
                st.caption("No tasks scheduled.")
            st.markdown(f"**Training** ({d['training_minutes']} min)")
            for s in d["training"]:
                st.link_button(f"{s['title']} · {s['start_time']} · {s['duration_min']} min · {s['mode']}", s["link"])
            if not d["training"]:
                st.caption("No sessions.")
            st.markdown("**Checkpoints**")
            for c in d["checkpoints"]:
                st.markdown(f"- ☑️ {c}")
    if p["blocked"] or p["unscheduled"]:
        st.error("At risk: " + "; ".join(f"{t['title']} ({t['reason']})" for t in p["blocked"] + p["unscheduled"]))
    st.download_button("Download plan (.md)", plan_markdown(p), file_name="first_week_plan.md")


# ------------------------------------------------------------------ chat
def page_chat():
    eid = need_employee()
    st.header("Ask the Guide")
    st.caption("Answers come only from the uploaded documents, with sources. Unclear answers are escalated to HR.")
    for m in api("GET", f"/employees/{eid}/chat") or []:
        with st.chat_message(m["role"]):
            st.write(m["content"])
            if m["role"] == "assistant":
                r = m["review"] or {}
                if r.get("escalated"):
                    st.warning("Escalated to HR with retrieved evidence.")
                elif r:
                    st.caption(f"Response Review Agent: {r.get('verdict')} · confidence {r.get('confidence', 0):.2f} ({r.get('method')})")
                if m["sources"]:
                    with st.expander(f"Sources ({len(m['sources'])})"):
                        for s in sorted(m["sources"], key=lambda x: not x["cited"]):
                            st.markdown(f"**{s['label']}** {'✅ cited' if s['cited'] else ''} · `{s['source']}` › {s['section']} · relevance {s['score']}")
                            st.caption(s["text"])
    q = st.chat_input("Ask about policies, tasks, training…")
    if q:
        with st.spinner("Searching documents…"):
            api("POST", f"/employees/{eid}/chat", json={"question": q})
        st.rerun()


# ------------------------------------------------------------------ readiness pulse
def page_quiz():
    eid = need_employee()
    st.header("Readiness Pulse")
    if st.button("Start a new 5-question check", type="primary"):
        st.session_state["quiz"] = api("GET", f"/employees/{eid}/quiz")
        st.session_state["quiz_eid"] = eid
    quiz = st.session_state.get("quiz")
    if not quiz or st.session_state.get("quiz_eid") != eid:
        return
    answers = []
    with st.form("quiz"):
        for i, q in enumerate(quiz):
            choice = st.radio(q["question"], q["options"], index=None, key=f"q{i}")
            answers.append({"topic": q["topic"], "question": q["question"], "choice": choice or "", "answer": q["answer"]})
        done = st.form_submit_button("Submit", type="primary")
    if done:
        res = api("POST", f"/employees/{eid}/quiz/submit", json=answers)
        if res:
            st.metric("Score", f"{res['score']} / {res['total']}")
            if res["weak_topics"]:
                st.warning("Topics to reinforce: " + ", ".join(res["weak_topics"]) + " (they will appear in your plan checkpoints).")
            else:
                st.success("You're on track.")
            st.session_state.pop("quiz")


# ------------------------------------------------------------------ HR: dashboard
def page_hr_dashboard():
    st.header("HR Dashboard")
    emps = api("GET", "/employees") or []
    docs = api("GET", "/documents") or []
    tasks = api("GET", "/tasks") or []
    open_q = api("GET", "/questions?status=open") or []
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Employees onboarding", len(emps))
    c2.metric("Documents ingested", len(docs))
    c3.metric("Tasks extracted", len(tasks))
    c4.metric("Open escalations", len(open_q))
    st.divider()
    left, right = st.columns([1.3, 1])
    with left:
        st.subheader("Employees")
        if emps:
            st.dataframe(pd.DataFrame(emps)[["name", "role", "location", "joining_date", "pace"]],
                        use_container_width=True, hide_index=True)
        else:
            st.info("No employees yet — add one on **Onboard Employee**.")
    with right:
        st.subheader("Open escalations")
        if open_q:
            for q in open_q[:5]:
                st.markdown(f"🚨 **#{q['id']}** {q['question'][:90]}")
        else:
            st.success("No open escalations. 🎉")


# ------------------------------------------------------------------ HR: onboard employee (role-aware intake)
def page_onboard():
    st.header("Onboard a New Employee")
    st.caption("Creates the employee profile, assigns role-specific tasks, and sets up their login.")
    with st.form("intake"):
        name = st.text_input("Full name")
        c1, c2 = st.columns(2)
        role = c1.selectbox("Role", ROLES)
        custom = c1.text_input("If Other, enter the role")
        location = c2.selectbox("Location", LOCATIONS)
        jd = c2.date_input("Joining date", date.today())
        pace = c1.select_slider("Preferred learning pace", ["Slow", "Normal", "Fast"], value="Normal")
        exp = st.text_area("Prior experience", placeholder="e.g. 3 years Java, new to cloud")
        st.markdown("**Employee login**")
        c3, c4 = st.columns(2)
        email = c3.text_input("Login email")
        pw = c4.text_input("Temporary password", type="password")
        submitted = st.form_submit_button("Create employee", type="primary")
    if submitted:
        if not name.strip() or not email.strip() or not pw.strip():
            st.warning("Name, email and password are required.")
        else:
            body = dict(name=name, role=custom.strip() if role == "Other" and custom.strip() else role,
                       location=location, joining_date=jd.isoformat(), experience=exp, pace=pace,
                       email=email.strip().lower(), password=pw)
            res = api("POST", "/employees", json=body)
            if res:
                st.success(f"{res['name']} can now log in with {email.strip().lower()}.")

    st.divider()
    st.subheader("Existing employees")
    emps = api("GET", "/employees") or []
    if emps:
        pick = st.selectbox("Edit an employee", [""] + [f"{e['id']} · {e['name']} ({e['role']})" for e in emps])
        if pick:
            emp = next(e for e in emps if e["id"] == int(pick.split(" · ")[0]))
            with st.form("edit"):
                name = st.text_input("Full name", emp["name"])
                c1, c2 = st.columns(2)
                role_opts = ROLES if emp["role"] in ROLES else ROLES + [emp["role"]]
                role = c1.selectbox("Role", role_opts, index=role_opts.index(emp["role"]))
                location = c2.selectbox("Location", LOCATIONS, index=LOCATIONS.index(emp["location"]) if emp["location"] in LOCATIONS else 0)
                jd = c2.date_input("Joining date", date.fromisoformat(emp["joining_date"]) if emp["joining_date"] else date.today())
                pace = c1.select_slider("Pace", ["Slow", "Normal", "Fast"], value=emp["pace"])
                exp = st.text_area("Prior experience", emp["experience"])
                save = st.form_submit_button("Save changes", type="primary")
            if save:
                body = dict(name=name, role=role, location=location, joining_date=jd.isoformat(), experience=exp, pace=pace)
                if api("PATCH", f"/employees/{emp['id']}", json=body):
                    st.success("Updated.")
                    st.rerun()


# ------------------------------------------------------------------ HR: documents & tasks
def page_documents():
    st.header("Documents & Extracted Tasks")
    t1, t2, t3 = st.tabs(["Upload & ingest", "Documents", "Extracted tasks"])
    with t1:
        c1, c2 = st.columns([3, 1])
        dtype = c1.selectbox("Document type", DOC_TYPES)
        files = c1.file_uploader("Policies, FAQs, role profiles, training schedules, org files",
                                 type=["pdf", "docx", "md", "txt", "csv"], accept_multiple_files=True)
        if c1.button("Ingest files", type="primary") and files:
            for f in files:
                with st.spinner(f"Ingesting {f.name}…"):
                    res = api("POST", "/documents/upload", files={"file": (f.name, f.getvalue())}, data={"doc_type": dtype})
                if res:
                    st.success(f"{res['filename']}: {res['chunks']} chunks, {res['tasks_found']} tasks ({res['extraction_method']})")
        c2.markdown("&nbsp;")
        if c2.button("Load sample data"):
            with st.spinner("Embedding sample documents (first run downloads the embedding model)…"):
                res = api("POST", "/documents/load-samples", timeout=600)
            if res:
                st.success(f"Ingested {len(res['ingested'])} sample file(s).")
    with t2:
        docs = api("GET", "/documents") or []
        if docs:
            st.dataframe(pd.DataFrame(docs), use_container_width=True, hide_index=True)
            pick = st.selectbox("Delete a document", [""] + [f"{d['id']} · {d['filename']}" for d in docs])
            if pick and st.button("Delete"):
                api("DELETE", f"/documents/{pick.split(' · ')[0]}")
                st.rerun()
        else:
            st.info("No documents yet.")
    with t3:
        tasks = api("GET", "/tasks") or []
        if tasks:
            df = pd.DataFrame(tasks)[["title", "owner", "deadline_day", "role", "category", "mandatory", "depends_on_text", "source_doc", "extracted_by"]]
            st.dataframe(df, use_container_width=True, hide_index=True)
        else:
            st.info("No tasks extracted yet.")


# ------------------------------------------------------------------ HR: escalations
def page_escalations():
    st.header("Escalations")
    qs = api("GET", "/questions?status=open") or []
    if not qs:
        st.success("No open escalations. 🎉")
    for q in qs:
        with st.expander(f"#{q['id']} · {q['question'][:90]}"):
            st.write(q["question"])
            for ev in q["evidence"]:
                st.caption(f"`{ev['source']}` › {ev['section']} (relevance {ev['score']}): {ev['text']}")
            res = st.text_area("Resolution", key=f"res{q['id']}")
            if st.button("Mark resolved", key=f"rb{q['id']}") and res.strip():
                api("POST", f"/questions/{q['id']}/resolve", json={"resolution": res})
                st.rerun()
    with st.expander("Resolved history"):
        resolved = api("GET", "/questions?status=resolved") or []
        for q in resolved:
            st.markdown(f"✅ **#{q['id']}** {q['question'][:90]}  \n_{q['resolution']}_")


# ------------------------------------------------------------------ router
if "token" not in st.session_state:
    login_screen()

page = sidebar()
EMP_ROUTES = {"🏠 Dashboard": page_emp_dashboard, "✅ My Checklist": page_checklist,
             "🗓️ First-Week Plan": page_plan, "💬 Ask the Guide": page_chat, "🧠 Readiness Pulse": page_quiz}
HR_ROUTES = {"🏠 HR Dashboard": page_hr_dashboard, "🧑‍💼 Onboard Employee": page_onboard,
            "📁 Documents & Tasks": page_documents, "🚨 Escalations": page_escalations}
(HR_ROUTES if st.session_state["role"] == "hr" else EMP_ROUTES)[page]()
