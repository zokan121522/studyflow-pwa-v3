# backend/models.py
"""Data models and SQL DDL for StudyFlow PWA v3."""

from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from typing import Optional, List, Dict, Any
import json


# ─── Enums (Agenda) ───────────────────────────────────────────────
class SessionCategory(str, Enum):
    FORMAL_STUDY = "formal_study"      # 🎓
    SELF_STUDY = "self_study"          # 📚
    WORK = "work"                      # 💼
    LANGUAGE = "language"              # 🌐
    HEALTH = "health"                  # 🏋️
    MIND = "mind"                      # 🧠
    PROJECT = "project"                # ⚡


class SessionState(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


# ─── Helpers ──────────────────────────────────────────────────────
def iso_week_key(d: Optional[date] = None) -> str:
    """Return ISO week key like '2026-W23'."""
    d = d or date.today()
    iso = d.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


# ============================== SQL DDL ==============================
# Tables are created in database.py _create_tables()
# This file provides Python dataclasses for type safety and serialization


@dataclass
class User:
    id: int
    email: str
    name: Optional[str] = None
    avatar_url: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            'id': self.id,
            'email': self.email,
            'name': self.name,
            'avatar_url': self.avatar_url,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }

    @classmethod
    def from_row(cls, row: Dict[str, Any]) -> 'User':
        return cls(
            id=row['id'],
            email=row['email'],
            name=row.get('name'),
            avatar_url=row.get('avatar_url'),
            created_at=row.get('created_at'),
            updated_at=row.get('updated_at'),
        )


@dataclass
class Habit:
    id: int
    user_id: int
    name: str
    description: Optional[str] = None
    frequency: str = 'daily'
    target_count: int = 1
    color: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            'id': self.id,
            'user_id': self.user_id,
            'name': self.name,
            'description': self.description,
            'frequency': self.frequency,
            'target_count': self.target_count,
            'color': self.color,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }

    @classmethod
    def from_row(cls, row: Dict[str, Any]) -> 'Habit':
        return cls(
            id=row['id'],
            user_id=row['user_id'],
            name=row['name'],
            description=row.get('description'),
            frequency=row.get('frequency', 'daily'),
            target_count=row.get('target_count', 1),
            color=row.get('color'),
            created_at=row.get('created_at'),
            updated_at=row.get('updated_at'),
        )


@dataclass
class HabitEntry:
    id: int
    habit_id: int
    user_id: int
    date: datetime
    count: int = 1
    completed: bool = False
    created_at: Optional[datetime] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            'id': self.id,
            'habit_id': self.habit_id,
            'user_id': self.user_id,
            'date': self.date.isoformat() if self.date else None,
            'count': self.count,
            'completed': self.completed,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }

    @classmethod
    def from_row(cls, row: Dict[str, Any]) -> 'HabitEntry':
        return cls(
            id=row['id'],
            habit_id=row['habit_id'],
            user_id=row['user_id'],
            date=row['date'],
            count=row.get('count', 1),
            completed=row.get('completed', False),
            created_at=row.get('created_at'),
        )


@dataclass
class Course:
    id: int
    user_id: int
    title: str
    description: Optional[str] = None
    color: Optional[str] = None
    progress: int = 0
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            'id': self.id,
            'user_id': self.user_id,
            'title': self.title,
            'description': self.description,
            'color': self.color,
            'progress': self.progress,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }

    @classmethod
    def from_row(cls, row: Dict[str, Any]) -> 'Course':
        return cls(
            id=row['id'],
            user_id=row['user_id'],
            title=row['title'],
            description=row.get('description'),
            color=row.get('color'),
            progress=row.get('progress', 0),
            created_at=row.get('created_at'),
            updated_at=row.get('updated_at'),
        )


@dataclass
class Topic:
    id: int
    course_id: int
    user_id: int
    title: str
    description: Optional[str] = None
    notes: Optional[str] = ''
    order_index: int = 0
    status: str = 'pending'
    estimated_minutes: Optional[int] = None
    actual_minutes: int = 0
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            'id': self.id,
            'course_id': self.course_id,
            'user_id': self.user_id,
            'title': self.title,
            'description': self.description,
            'notes': self.notes or '',
            'order_index': self.order_index,
            'status': self.status,
            'estimated_minutes': self.estimated_minutes,
            'actual_minutes': self.actual_minutes,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }

    @classmethod
    def from_row(cls, row: Dict[str, Any]) -> 'Topic':
        return cls(
            id=row['id'],
            course_id=row['course_id'],
            user_id=row['user_id'],
            title=row['title'],
            description=row.get('description'),
            notes=row.get('notes', '') or '',
            order_index=row.get('order_index', 0),
            status=row.get('status', 'pending'),
            estimated_minutes=row.get('estimated_minutes'),
            actual_minutes=row.get('actual_minutes', 0),
            created_at=row.get('created_at'),
            updated_at=row.get('updated_at'),
        )


@dataclass
class Block:
    """Content block (markdown | content | separator | pdf-ref | youtube | audio).

    v2-compatible fields + v3-friendly url/color/collapsed. topic_id
    is optional for course-level blocks.
    """
    id: int
    user_id: int
    course_id: int
    topic_id: Optional[int] = None
    type: str = 'markdown'
    title: str = ''
    content: str = ''
    url: str = ''
    done: bool = False
    order_index: int = 0
    color: str = ''
    collapsed: bool = False
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            'id': self.id, 'user_id': self.user_id,
            'course_id': self.course_id, 'topic_id': self.topic_id,
            'type': self.type, 'title': self.title,
            'content': self.content, 'url': self.url,
            'done': self.done, 'order_index': self.order_index,
            'color': self.color, 'collapsed': self.collapsed,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }

    @classmethod
    def from_row(cls, row: Dict[str, Any]) -> 'Block':
        return cls(
            id=row['id'], user_id=row['user_id'], course_id=row['course_id'],
            topic_id=row.get('topic_id'),
            type=row.get('type', 'markdown'),
            title=row.get('title') or '',
            content=row.get('content') or '',
            url=row.get('url') or '',
            done=bool(row.get('done', False)),
            order_index=row.get('order_index', 0) or 0,
            color=row.get('color') or '',
            collapsed=bool(row.get('collapsed', False)),
            created_at=row.get('created_at'),
            updated_at=row.get('updated_at'),
        )


@dataclass
class PDF:
    id: int
    user_id: int
    course_id: Optional[int] = None
    topic_id: Optional[int] = None
    filename: str = ''
    original_name: str = ''
    file_size: Optional[int] = None
    page_count: Optional[int] = None
    storage_path: Optional[str] = None
    created_at: Optional[datetime] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            'id': self.id,
            'user_id': self.user_id,
            'course_id': self.course_id,
            'topic_id': self.topic_id,
            'filename': self.filename,
            'original_name': self.original_name,
            'file_size': self.file_size,
            'page_count': self.page_count,
            'storage_path': self.storage_path,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }

    @classmethod
    def from_row(cls, row: Dict[str, Any]) -> 'PDF':
        return cls(
            id=row['id'],
            user_id=row['user_id'],
            course_id=row.get('course_id'),
            topic_id=row.get('topic_id'),
            filename=row['filename'],
            original_name=row['original_name'],
            file_size=row.get('file_size'),
            page_count=row.get('page_count'),
            storage_path=row.get('storage_path'),
            created_at=row.get('created_at'),
        )


@dataclass
class QuizQuestion:
    id: int
    user_id: int
    course_id: int
    topic_id: Optional[int] = None
    block_id: Optional[int] = None
    question: str = ''
    options: List[str] = None
    correct_answer: int = 0
    explanation: Optional[str] = None
    difficulty: str = 'medium'
    created_at: Optional[datetime] = None

    def __post_init__(self):
        if self.options is None:
            self.options = []

    def to_dict(self) -> Dict[str, Any]:
        return {
            'id': self.id,
            'user_id': self.user_id,
            'course_id': self.course_id,
            'topic_id': self.topic_id,
            'block_id': self.block_id,
            'question': self.question,
            'options': self.options,
            'correct_answer': self.correct_answer,
            'explanation': self.explanation,
            'difficulty': self.difficulty,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }

    @classmethod
    def from_row(cls, row: Dict[str, Any]) -> 'QuizQuestion':
        options = row['options']
        if isinstance(options, str):
            options = json.loads(options)
        return cls(
            id=row['id'],
            user_id=row['user_id'],
            course_id=row['course_id'],
            topic_id=row.get('topic_id'),
            block_id=row.get('block_id'),
            question=row['question'],
            options=options,
            correct_answer=row['correct_answer'],
            explanation=row.get('explanation'),
            difficulty=row.get('difficulty', 'medium'),
            created_at=row.get('created_at'),
        )


@dataclass
class QuizResult:
    id: int
    user_id: int
    course_id: int
    topic_id: Optional[int] = None
    block_id: Optional[int] = None
    question_id: int = 0
    selected_answer: Optional[int] = None
    is_correct: Optional[bool] = None
    time_taken_ms: Optional[int] = None
    created_at: Optional[datetime] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            'id': self.id,
            'user_id': self.user_id,
            'course_id': self.course_id,
            'topic_id': self.topic_id,
            'block_id': self.block_id,
            'question_id': self.question_id,
            'selected_answer': self.selected_answer,
            'is_correct': self.is_correct,
            'time_taken_ms': self.time_taken_ms,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }

    @classmethod
    def from_row(cls, row: Dict[str, Any]) -> 'QuizResult':
        return cls(
            id=row['id'],
            user_id=row['user_id'],
            course_id=row['course_id'],
            topic_id=row.get('topic_id'),
            block_id=row.get('block_id'),
            question_id=row['question_id'],
            selected_answer=row.get('selected_answer'),
            is_correct=row.get('is_correct'),
            time_taken_ms=row.get('time_taken_ms'),
            created_at=row.get('created_at'),
        )


@dataclass
class Todo:
    id: int
    user_id: int
    title: str
    description: Optional[str] = None
    completed: bool = False
    priority: str = 'medium'
    due_date: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            'id': self.id,
            'user_id': self.user_id,
            'title': self.title,
            'description': self.description,
            'completed': self.completed,
            'priority': self.priority,
            'due_date': self.due_date.isoformat() if self.due_date else None,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }

    @classmethod
    def from_row(cls, row: Dict[str, Any]) -> 'Todo':
        return cls(
            id=row['id'],
            user_id=row['user_id'],
            title=row['title'],
            description=row.get('description'),
            completed=row.get('completed', False),
            priority=row.get('priority', 'medium'),
            due_date=row.get('due_date'),
            created_at=row.get('created_at'),
            updated_at=row.get('updated_at'),
        )


@dataclass
class AudioFile:
    id: int
    user_id: int
    title: str
    text_content: str
    audio_url: Optional[str] = None
    duration_seconds: Optional[int] = None
    voice: Optional[str] = None
    language: str = 'es'
    created_at: Optional[datetime] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            'id': self.id,
            'user_id': self.user_id,
            'title': self.title,
            'text_content': self.text_content,
            'audio_url': self.audio_url,
            'duration_seconds': self.duration_seconds,
            'voice': self.voice,
            'language': self.language,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }

    @classmethod
    def from_row(cls, row: Dict[str, Any]) -> 'AudioFile':
        return cls(
            id=row['id'],
            user_id=row['user_id'],
            title=row['title'],
            text_content=row['text_content'],
            audio_url=row.get('audio_url'),
            duration_seconds=row.get('duration_seconds'),
            voice=row.get('voice'),
            language=row.get('language', 'es'),
            created_at=row.get('created_at'),
        )


# ============================== TABLE NAMES ==============================
TABLES = {
    'users': 'users',
    'sessions': 'sessions',
    'habits': 'habits',
    'habit_entries': 'habit_entries',
    'courses': 'courses',
    'topics': 'topics',
    'blocks': 'blocks',
    'pdfs': 'pdfs',
    'quiz_questions': 'quiz_questions',
    'quiz_results': 'quiz_results',
    'todos': 'todos',
    'audio_files': 'audio_files',
}