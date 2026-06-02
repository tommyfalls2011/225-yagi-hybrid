from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]

DATA_DIR = PROJECT_DIR / "data"
MODELS_DIR = PROJECT_DIR / "models"
LOGS_DIR = PROJECT_DIR / "logs"
BACKUPS_DIR = PROJECT_DIR / "backups"

DB_PATH = DATA_DIR / "auto7_history.db"


def ensure_dirs():
    for p in [DATA_DIR, MODELS_DIR, LOGS_DIR, BACKUPS_DIR]:
        p.mkdir(parents=True, exist_ok=True)


def print_paths():
    ensure_dirs()

    print("Project paths")
    print("=============")
    print(f"PROJECT_DIR: {PROJECT_DIR}")
    print(f"DATA_DIR:    {DATA_DIR}")
    print(f"MODELS_DIR:  {MODELS_DIR}")
    print(f"LOGS_DIR:    {LOGS_DIR}")
    print(f"BACKUPS_DIR: {BACKUPS_DIR}")
    print(f"DB_PATH:     {DB_PATH}")
