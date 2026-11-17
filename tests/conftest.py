import os
import tempfile
from pathlib import Path

# Point the app at a throwaway database before any app module is imported.
_tmp = Path(tempfile.mkdtemp()) / "test.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp.as_posix()}"
os.environ["WHOOP_CLIENT_ID"] = "test-client"
os.environ["WHOOP_CLIENT_SECRET"] = "test-secret"

import pytest  # noqa: E402

from app.db import SessionLocal, init_db  # noqa: E402
from app.models import Base  # noqa: E402

init_db()


@pytest.fixture(autouse=True)
def clean_db():
    """Every test starts from empty tables, so tests can't affect each other."""
    with SessionLocal() as s:
        for table in reversed(Base.metadata.sorted_tables):
            s.execute(table.delete())
        s.commit()
    yield
