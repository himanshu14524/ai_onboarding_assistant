from datetime import datetime
from typing import Optional

from sqlalchemy import JSON, Boolean, DateTime, Integer, String, Text, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from .config import DB_URL

engine = create_engine(
    DB_URL, connect_args={"check_same_thread": False} if DB_URL.startswith("sqlite") else {}
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(200), unique=True)
    password_hash: Mapped[str] = mapped_column(String(300))
    role: Mapped[str] = mapped_column(String(20))  # "hr" or "employee"
    employee_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class AuthToken(Base):
    __tablename__ = "auth_tokens"
    token: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Employee(Base):
    __tablename__ = "employees"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    role: Mapped[str] = mapped_column(String(120))
    location: Mapped[str] = mapped_column(String(120), default="")
    joining_date: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    experience: Mapped[str] = mapped_column(Text, default="")
    pace: Mapped[str] = mapped_column(String(20), default="Normal")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Document(Base):
    __tablename__ = "documents"
    id: Mapped[int] = mapped_column(primary_key=True)
    filename: Mapped[str] = mapped_column(String(255), unique=True)
    doc_type: Mapped[str] = mapped_column(String(40))
    chunks: Mapped[int] = mapped_column(Integer, default=0)
    tasks_found: Mapped[int] = mapped_column(Integer, default=0)
    extraction_method: Mapped[str] = mapped_column(String(20), default="")
    uploaded_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Task(Base):
    __tablename__ = "tasks"
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(300))
    owner: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    deadline_day: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    category: Mapped[str] = mapped_column(String(120), default="General")
    role: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)  # None = everyone
    depends_on_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    depends_on: Mapped[list] = mapped_column(JSON, default=list)  # resolved task ids
    mandatory: Mapped[bool] = mapped_column(Boolean, default=True)
    source_doc: Mapped[str] = mapped_column(String(255), default="")
    source_snippet: Mapped[str] = mapped_column(Text, default="")
    extracted_by: Mapped[str] = mapped_column(String(20), default="rules")


class EmployeeTask(Base):
    __tablename__ = "employee_tasks"
    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int] = mapped_column(Integer, index=True)
    task_id: Mapped[int] = mapped_column(Integer, index=True)
    status: Mapped[str] = mapped_column(String(20), default="todo")  # todo | done | delayed
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ChatMessage(Base):
    __tablename__ = "chat_messages"
    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int] = mapped_column(Integer, index=True)
    role: Mapped[str] = mapped_column(String(20))  # user | assistant
    content: Mapped[str] = mapped_column(Text)
    sources: Mapped[list] = mapped_column(JSON, default=list)
    review: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class OpenQuestion(Base):
    __tablename__ = "open_questions"
    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int] = mapped_column(Integer, index=True)
    question: Mapped[str] = mapped_column(Text)
    evidence: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(20), default="open")  # open | resolved
    resolution: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ReadinessResult(Base):
    __tablename__ = "readiness_results"
    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int] = mapped_column(Integer, index=True)
    topic: Mapped[str] = mapped_column(String(120))
    correct: Mapped[int] = mapped_column(Integer)
    total: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


def init_db():
    Base.metadata.create_all(engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ---- serializers -------------------------------------------------------------
def user_dict(u: User) -> dict:
    return dict(id=u.id, email=u.email, role=u.role, employee_id=u.employee_id)


def emp_dict(e: Employee) -> dict:
    return dict(id=e.id, name=e.name, role=e.role, location=e.location,
                joining_date=e.joining_date, experience=e.experience, pace=e.pace)


def task_dict(t: Task) -> dict:
    return dict(id=t.id, title=t.title, owner=t.owner, deadline_day=t.deadline_day,
                category=t.category, role=t.role, depends_on=t.depends_on or [],
                depends_on_text=t.depends_on_text, mandatory=t.mandatory,
                source_doc=t.source_doc, source_snippet=t.source_snippet,
                extracted_by=t.extracted_by)
