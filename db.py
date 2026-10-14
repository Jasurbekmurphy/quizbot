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
conn.commit()


def create_quiz(owner_id, title, questions, time_limit=30):
    cur = conn.execute("INSERT INTO quizzes(owner_id, title, time_limit) VALUES (?,?,?)", (owner_id, title, time_limit))
    quiz_id = cur.lastrowid
    conn.executemany(
        "INSERT INTO questions(quiz_id, pos, text, options, correct, explanation) VALUES (?,?,?,?,?,?)",
        [(quiz_id, i, q["text"], json.dumps(q["options"], ensure_ascii=False), q["correct"], q.get("explanation", ""))
         for i, q in enumerate(questions)],
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


def quiz_questions(quiz_id):
    rows = conn.execute("SELECT * FROM questions WHERE quiz_id=? ORDER BY pos", (quiz_id,))
    return [dict(text=r["text"], options=json.loads(r["options"]), correct=r["correct"], explanation=r["explanation"])
            for r in rows]


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
