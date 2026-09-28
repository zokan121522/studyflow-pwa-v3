# backend/database.py
"""PostgreSQL connection manager for StudyFlow PWA v3."""

import os
import psycopg2
from psycopg2 import pool
from psycopg2.extras import RealDictCursor
from contextlib import contextmanager
from typing import Optional, List, Dict, Any


# Global connection pool
_connection_pool: Optional[pool.ThreadedConnectionPool] = None


def init_db() -> None:
    """Initialize the connection pool and create tables if they don't exist."""
    global _connection_pool

    database_url = os.environ.get('DATABASE_URL')
    if not database_url:
        raise RuntimeError('DATABASE_URL environment variable not set')

    _connection_pool = pool.ThreadedConnectionPool(
        minconn=1,
        maxconn=10,
        dsn=database_url,
        cursor_factory=RealDictCursor
    )

    # Create tables
    with get_db() as conn:
        with conn.cursor() as cur:
            _create_tables(cur)
        conn.commit()

    # Seed local user (id=1) for single-user mode
    _seed_local_user()


def _seed_local_user() -> None:
    """Ensure the implicit local user (id=1) exists so single-user mode works.

    Uses ON CONFLICT for idempotency. Resets the users.id sequence to avoid
    colliding with id=1 on future inserts.
    """
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO users (id, email, password_hash, name)
                VALUES (1, 'local@studyflow.app', 'local-no-auth', 'Usuario Local')
                ON CONFLICT (id) DO NOTHING
                """
            )
            cur.execute(
                """
                SELECT setval(
                    pg_get_serial_sequence('users', 'id'),
                    GREATEST((SELECT MAX(id) FROM users), 1)
                )
                """
            )
        conn.commit()


def _create_tables(cur) -> None:
    """Create all tables if they don't exist."""
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            email VARCHAR(255) UNIQUE NOT NULL,
            password_hash VARCHAR(255) NOT NULL,
            name VARCHAR(100),
            avatar_url VARCHAR(500),
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            id SERIAL PRIMARY KEY,
            user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
            title VARCHAR(255) NOT NULL,
            description TEXT,
            category VARCHAR(50),
            start_time TIMESTAMP WITH TIME ZONE NOT NULL,
            end_time TIMESTAMP WITH TIME ZONE NOT NULL,
            color VARCHAR(20),
            is_recurring BOOLEAN DEFAULT FALSE,
            recurrence_rule VARCHAR(100),
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS habits (
            id SERIAL PRIMARY KEY,
            user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
            name VARCHAR(255) NOT NULL,
            description TEXT,
            frequency VARCHAR(20) DEFAULT 'daily',
            target_count INTEGER DEFAULT 1,
            color VARCHAR(20),
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS habit_entries (
            id SERIAL PRIMARY KEY,
            habit_id INTEGER REFERENCES habits(id) ON DELETE CASCADE,
            user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
            date DATE NOT NULL,
            count INTEGER DEFAULT 1,
            completed BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            UNIQUE(habit_id, date)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS courses (
            id SERIAL PRIMARY KEY,
            user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
            title VARCHAR(255) NOT NULL,
            description TEXT,
            color VARCHAR(20),
            progress INTEGER DEFAULT 0,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS topics (
            id SERIAL PRIMARY KEY,
            course_id INTEGER REFERENCES courses(id) ON DELETE CASCADE,
            user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
            title VARCHAR(255) NOT NULL,
            description TEXT,
            order_index INTEGER DEFAULT 0,
            status VARCHAR(20) DEFAULT 'pending',
            estimated_minutes INTEGER,
            actual_minutes INTEGER DEFAULT 0,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS pdfs (
            id SERIAL PRIMARY KEY,
            user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
            course_id INTEGER REFERENCES courses(id) ON DELETE SET NULL,
            topic_id INTEGER REFERENCES topics(id) ON DELETE SET NULL,
            filename VARCHAR(255) NOT NULL,
            original_name VARCHAR(255) NOT NULL,
            file_size BIGINT,
            page_count INTEGER,
            storage_path VARCHAR(500),
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS quiz_questions (
            id SERIAL PRIMARY KEY,
            user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
            course_id INTEGER REFERENCES courses(id) ON DELETE CASCADE,
            topic_id INTEGER REFERENCES topics(id) ON DELETE SET NULL,
            question TEXT NOT NULL,
            options JSONB NOT NULL,
            correct_answer INTEGER NOT NULL,
            explanation TEXT,
            difficulty VARCHAR(20) DEFAULT 'medium',
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS quiz_results (
            id SERIAL PRIMARY KEY,
            user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
            course_id INTEGER REFERENCES courses(id) ON DELETE CASCADE,
            topic_id INTEGER REFERENCES topics(id) ON DELETE SET NULL,
            question_id INTEGER REFERENCES quiz_questions(id) ON DELETE CASCADE,
            selected_answer INTEGER,
            is_correct BOOLEAN,
            time_taken_ms INTEGER,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS todos (
            id SERIAL PRIMARY KEY,
            user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
            title VARCHAR(255) NOT NULL,
            description TEXT,
            completed BOOLEAN DEFAULT FALSE,
            priority VARCHAR(20) DEFAULT 'medium',
            due_date TIMESTAMP WITH TIME ZONE,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS audio_files (
            id SERIAL PRIMARY KEY,
            user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
            title VARCHAR(255) NOT NULL,
            text_content TEXT,
            audio_url VARCHAR(500),
            duration_seconds INTEGER,
            voice VARCHAR(50),
            language VARCHAR(10) DEFAULT 'es',
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        )
    """)

    # Create indexes for common queries
    cur.execute("CREATE INDEX IF NOT EXISTS idx_sessions_user_time ON sessions(user_id, start_time)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_habit_entries_user_date ON habit_entries(user_id, date)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_topics_course_order ON topics(course_id, order_index)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_todos_user_completed ON todos(user_id, completed)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_quiz_results_user_time ON quiz_results(user_id, created_at)")


@contextmanager
def get_db():
    """Get a database connection from the pool."""
    global _connection_pool
    if _connection_pool is None:
        init_db()

    conn = _connection_pool.getconn()
    try:
        yield conn
    finally:
        _connection_pool.putconn(conn)


def execute(query: str, params: tuple = None) -> int:
    """Execute a query (INSERT, UPDATE, DELETE) and return row count."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(query, params)
            conn.commit()
            return cur.rowcount


def fetchone(query: str, params: tuple = None) -> Optional[Dict[str, Any]]:
    """Fetch a single row as a dictionary."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(query, params)
            return cur.fetchone()


def fetchall(query: str, params: tuple = None) -> List[Dict[str, Any]]:
    """Fetch all rows as a list of dictionaries."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(query, params)
            return cur.fetchall()


def fetchone_raw(query: str, params: tuple = None):
    """Fetch a single row as a raw tuple (for counts, etc)."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(query, params)
            return cur.fetchone()


def close_pool() -> None:
    """Close the connection pool."""
    global _connection_pool
    if _connection_pool:
        _connection_pool.closeall()
        _connection_pool = None