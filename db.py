import json
import sqlite3

from config import DB_PATH

conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row
conn.executescript(
    """
    CREATE TABLE IF NOT EXISTS quizzes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        owner_id INTEGER NOT NULL,
        title TEXT NOT NULL,
        time_limit INTEGER NOT NULL DEFAULT 30,     -- har savolga soniya
        shuffle_q INTEGER NOT NULL DEFAULT 1,       -- savollarni aralashtirish
        shuffle_a INTEGER NOT NULL DEFAULT 1,       -- javoblarni aralashtirish
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS questions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        quiz_id INTEGER NOT NULL REFERENCES quizzes(id) ON DELETE CASCADE,
        pos INTEGER NOT NULL,
        text TEXT NOT NULL,
        options TEXT NOT NULL,                      -- JSON ro'yxat
        correct INTEGER NOT NULL,
        explanation TEXT NOT NULL DEFAULT ''
    );
    CREATE TABLE IF NOT EXISTS results (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        quiz_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        correct INTEGER NOT NULL,
        total INTEGER NOT NULL,
        seconds REAL NOT NULL,
        chat_type TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    """
)
conn.execute("PRAGMA foreign_keys = ON")
conn.executescript(
    """
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        name TEXT NOT NULL DEFAULT '',
        username TEXT NOT NULL DEFAULT '',
        first_seen TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        last_seen TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS chats (
        chat_id INTEGER PRIMARY KEY,
        title TEXT NOT NULL DEFAULT '',
        type TEXT NOT NULL,
        first_seen TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        last_seen TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    -- statistika qo'shilishidan oldingi foydalanuvchilar: test egalari va test yechganlar
    INSERT OR IGNORE INTO users(user_id, first_seen, last_seen) SELECT owner_id, MIN(created_at), MAX(created_at) FROM quizzes GROUP BY owner_id;
    INSERT OR IGNORE INTO users(user_id, name, first_seen, last_seen) SELECT user_id, name, MIN(created_at), MAX(created_at) FROM results GROUP BY user_id;
    """
)
# Har savolga alohida vaqt (NULL bo'lsa testning umumiy vaqti ishlatiladi)
if "time_limit" not in [r["name"] for r in conn.execute("PRAGMA table_info(questions)")]:
    conn.execute("ALTER TABLE questions ADD COLUMN time_limit INTEGER")
conn.commit()


def create_quiz(owner_id, title, questions, time_limit=30):
    cur = conn.execute("INSERT INTO quizzes(owner_id, title, time_limit) VALUES (?,?,?)", (owner_id, title, time_limit))
    quiz_id = cur.lastrowid
    conn.executemany(
        "INSERT INTO questions(quiz_id, pos, text, options, correct, explanation, time_limit) VALUES (?,?,?,?,?,?,?)",
        [(quiz_id, i, q["text"], json.dumps(q["options"], ensure_ascii=False), q["correct"], q.get("explanation", ""),
          q.get("time_limit")) for i, q in enumerate(questions)],
    )
    conn.commit()
    return quiz_id


def get_quiz(quiz_id):
    return conn.execute("SELECT * FROM quizzes WHERE id=?", (quiz_id,)).fetchone()


def update_quiz(quiz_id, **fields):
    allowed = {"title", "time_limit", "shuffle_q", "shuffle_a"}
    sets = ", ".join(f"{k}=?" for k in fields if k in allowed)
    conn.execute(f"UPDATE quizzes SET {sets} WHERE id=?", [v for k, v in fields.items() if k in allowed] + [quiz_id])
    conn.commit()


def delete_quiz(quiz_id):
    conn.execute("DELETE FROM quizzes WHERE id=?", (quiz_id,))
    conn.commit()


def user_quizzes(owner_id):
    return list(conn.execute(
        "SELECT q.*, (SELECT COUNT(*) FROM questions WHERE quiz_id=q.id) AS n FROM quizzes q "
        "WHERE owner_id=? ORDER BY id DESC", (owner_id,),
    ))


def _question(r):
    return dict(id=r["id"], quiz_id=r["quiz_id"], pos=r["pos"], text=r["text"], options=json.loads(r["options"]),
                correct=r["correct"], explanation=r["explanation"], time_limit=r["time_limit"])


def quiz_questions(quiz_id):
    return [_question(r) for r in conn.execute("SELECT * FROM questions WHERE quiz_id=? ORDER BY pos", (quiz_id,))]


def get_question(question_id):
    r = conn.execute("SELECT * FROM questions WHERE id=?", (question_id,)).fetchone()
    return _question(r) if r else None


def set_question_time(question_id, seconds):
    conn.execute("UPDATE questions SET time_limit=? WHERE id=?", (seconds, question_id))
    conn.commit()


def set_all_times(quiz_id, seconds):
    """Hamma savollarga bir xil vaqt: alohida qo'yilgan vaqtlar o'chiriladi."""
    conn.execute("UPDATE quizzes SET time_limit=? WHERE id=?", (seconds, quiz_id))
    conn.execute("UPDATE questions SET time_limit=NULL WHERE quiz_id=?", (quiz_id,))
    conn.commit()


def question_count(quiz_id):
    return conn.execute("SELECT COUNT(*) FROM questions WHERE quiz_id=?", (quiz_id,)).fetchone()[0]


def save_result(quiz_id, user_id, name, correct, total, seconds, chat_type):
    conn.execute(
        "INSERT INTO results(quiz_id, user_id, name, correct, total, seconds, chat_type) VALUES (?,?,?,?,?,?,?)",
        (quiz_id, user_id, name, correct, total, seconds, chat_type),
    )
    conn.commit()


def result_count(quiz_id):
    return conn.execute("SELECT COUNT(DISTINCT user_id) FROM results WHERE quiz_id=?", (quiz_id,)).fetchone()[0]


# ---------- Statistika ----------

def touch_user(user_id, name, username):
    conn.execute(
        "INSERT INTO users(user_id, name, username) VALUES (?,?,?) "
        "ON CONFLICT(user_id) DO UPDATE SET name=excluded.name, username=excluded.username, last_seen=CURRENT_TIMESTAMP",
        (user_id, name, username or ""),
    )
    conn.commit()


def touch_chat(chat_id, title, chat_type):
    conn.execute(
        "INSERT INTO chats(chat_id, title, type) VALUES (?,?,?) "
        "ON CONFLICT(chat_id) DO UPDATE SET title=excluded.title, last_seen=CURRENT_TIMESTAMP",
        (chat_id, title or "", chat_type),
    )
    conn.commit()


def stats():
    one = lambda sql: conn.execute(sql).fetchone()[0]
    return dict(
        users=one("SELECT COUNT(*) FROM users"),
        today=one("SELECT COUNT(*) FROM users WHERE last_seen >= datetime('now', '-1 day')"),
        week=one("SELECT COUNT(*) FROM users WHERE last_seen >= datetime('now', '-7 days')"),
        month=one("SELECT COUNT(*) FROM users WHERE last_seen >= datetime('now', '-30 days')"),
        new_week=one("SELECT COUNT(*) FROM users WHERE first_seen >= datetime('now', '-7 days')"),
        creators=one("SELECT COUNT(DISTINCT owner_id) FROM quizzes"),
        quizzes=one("SELECT COUNT(*) FROM quizzes"),
        questions=one("SELECT COUNT(*) FROM questions"),
        solved=one("SELECT COUNT(*) FROM results"),
        solvers=one("SELECT COUNT(DISTINCT user_id) FROM results"),
        groups=one("SELECT COUNT(*) FROM chats WHERE type IN ('group', 'supergroup')"),
        top=list(conn.execute(
            "SELECT q.title, COUNT(r.id) AS n FROM results r JOIN quizzes q ON q.id = r.quiz_id "
            "GROUP BY r.quiz_id ORDER BY n DESC LIMIT 5"
        )),
    )
