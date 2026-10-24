import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
try:  # load settings from <project>/.env if python-dotenv is installed
    from dotenv import load_dotenv
    load_dotenv(BASE_DIR / ".env")
except ImportError:
    pass
DATA_DIR = BASE_DIR / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
VECTOR_DIR = DATA_DIR / "chroma"
SAMPLES_DIR = DATA_DIR / "samples"
TRAINING_CSV = DATA_DIR / "training_schedule.csv"

for _d in (UPLOAD_DIR, VECTOR_DIR, SAMPLES_DIR):
    _d.mkdir(parents=True, exist_ok=True)

DB_URL = os.getenv("DATABASE_URL", f"sqlite:///{DATA_DIR / 'onboarding.db'}")
EMBED_MODEL = os.getenv("EMBED_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

RETRIEVAL_K = 5
MIN_RELEVANCE = float(os.getenv("MIN_RELEVANCE", "0.25"))  # cosine similarity floor
HORIZON = 5  # first week = 5 working days
PACE_CAP = {"Slow": 2, "Normal": 3, "Fast": 5}          # optional tasks pulled per day
PACE_TRAINING_MIN = {"Slow": 120, "Normal": 240, "Fast": 360}
DOC_TYPES = ["policy", "faq", "role_profile", "training_schedule", "org_reference"]

# OpenAI-compatible proxy (LiteLLM) settings - used first when LLM_API_KEY is set
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://genailab.tcs.in")
LLM_MODEL = os.getenv("LLM_MODEL", "azure_ai/genailab-maas-DeepSeek-V3-0324")
LLM_VERIFY_SSL = os.getenv("LLM_VERIFY_SSL", "false").lower() in ("1", "true", "yes")
