"""SQLite conversation memory, project state, traces, call limits, review status (C4)."""
from __future__ import annotations
import json, sqlite3, time, datetime as dt
from contextlib import contextmanager
from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY, session_id TEXT, role TEXT, content TEXT, ts REAL);
CREATE TABLE IF NOT EXISTS project(session_id TEXT PRIMARY KEY, data TEXT, ts REAL);
CREATE TABLE IF NOT EXISTS traces(id INTEGER PRIMARY KEY, session_id TEXT, node TEXT, ms REAL, detail TEXT, ts REAL);
CREATE TABLE IF NOT EXISTS llm_calls(day TEXT, session_id TEXT, n INTEGER, PRIMARY KEY(day, session_id));
CREATE TABLE IF NOT EXISTS review(session_id TEXT PRIMARY KEY, status TEXT, reviewer TEXT, note TEXT, ts REAL);
"""


@contextmanager
def _db(path=None):
    path = path or config.DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.executescript(SCHEMA)
    try:
        yield con
        con.commit()
    finally:
        con.close()


class Memory:
    def __init__(self, session_id: str, path=None):
        self.sid, self.path = session_id, path

    def add_message(self, role: str, content: str):
        with _db(self.path) as c:
            c.execute("INSERT INTO messages(session_id,role,content,ts) VALUES(?,?,?,?)", (self.sid, role, content, time.time()))

    def history(self, limit: int = 50) -> list[dict]:
        with _db(self.path) as c:
            rows = c.execute("SELECT role,content FROM messages WHERE session_id=? ORDER BY id DESC LIMIT ?", (self.sid, limit)).fetchall()
        return [{"role": r, "content": t} for r, t in reversed(rows)]

    def get_project(self) -> dict:
        with _db(self.path) as c:
            row = c.execute("SELECT data FROM project WHERE session_id=?", (self.sid,)).fetchone()
        return json.loads(row[0]) if row else {}

    def update_project(self, **fields) -> dict:
        data = self.get_project()
        data.update({k: v for k, v in fields.items() if v is not None})
        with _db(self.path) as c:
            c.execute("INSERT OR REPLACE INTO project(session_id,data,ts) VALUES(?,?,?)", (self.sid, json.dumps(data), time.time()))
        return data

    def clear_project(self):
        with _db(self.path) as c:
            c.execute("DELETE FROM project WHERE session_id=?", (self.sid,))

    # ---- tracing
    def trace(self, node: str, ms: float, detail: str = ""):
        with _db(self.path) as c:
            c.execute("INSERT INTO traces(session_id,node,ms,detail,ts) VALUES(?,?,?,?,?)", (self.sid, node, ms, detail[:500], time.time()))

    def traces(self, limit: int = 60) -> list[dict]:
        with _db(self.path) as c:
            rows = c.execute("SELECT node,ms,detail,ts FROM traces WHERE session_id=? ORDER BY id DESC LIMIT ?", (self.sid, limit)).fetchall()
        return [dict(node=n, ms=round(m, 1), detail=d, ts=t) for n, m, d, t in rows]

    # ---- call limits (quota/cost runaway guardrail)
    def llm_calls_today(self) -> tuple[int, int]:
        day = dt.date.today().isoformat()
        with _db(self.path) as c:
            s = c.execute("SELECT n FROM llm_calls WHERE day=? AND session_id=?", (day, self.sid)).fetchone()
            t = c.execute("SELECT COALESCE(SUM(n),0) FROM llm_calls WHERE day=?", (day,)).fetchone()
        return (s[0] if s else 0), t[0]

    def can_call_llm(self) -> bool:
        s, t = self.llm_calls_today()
        return s < config.MAX_LLM_CALLS_PER_SESSION and t < config.MAX_LLM_CALLS_PER_DAY

    def count_llm_call(self):
        day = dt.date.today().isoformat()
        with _db(self.path) as c:
            c.execute("INSERT INTO llm_calls(day,session_id,n) VALUES(?,?,1) ON CONFLICT(day,session_id) DO UPDATE SET n=n+1", (day, self.sid))

    # ---- human review
    def get_review(self) -> dict:
        with _db(self.path) as c:
            r = c.execute("SELECT status,reviewer,note FROM review WHERE session_id=?", (self.sid,)).fetchone()
        return dict(status=r[0], reviewer=r[1], note=r[2]) if r else dict(status="preliminary", reviewer=None, note="")

    def set_review(self, status: str, reviewer: str = "", note: str = ""):
        assert status in ("preliminary", "pending_review", "approved")
        with _db(self.path) as c:
            c.execute("INSERT OR REPLACE INTO review(session_id,status,reviewer,note,ts) VALUES(?,?,?,?,?)", (self.sid, status, reviewer, note, time.time()))
