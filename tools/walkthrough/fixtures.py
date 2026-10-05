#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Datos de mentira para grabar los tutoriales.

Por qué existe esto
-------------------
Los vídeos se graban contra la app real, y la app real tiene dentro tus
asignaturas, tus títulos de sesión, tus hábitos y tu correo. El borroso por
CSS no sirve como protección: difumina los píxeles pero conserva la forma de
las letras, así que un OCR —o una persona con paciencia— los lee igual.

Aquí no se tapa nada. Se sustituye la respuesta del servidor por otra
inventada ANTES de que llegue al navegador, de forma que el nombre real
jamás existe en la imagen: no está difuminado, es que no está.

Uso
---
    python3 tools/walkthrough/fixtures.py      # volcar un JSON de ejemplo

Se aplica desde render.py con `page.route(...)`, nunca contra la BD: tu
Postgres no se toca y los datos reales no se mezclan con los falsos.
"""
from __future__ import annotations

import json
import pathlib
import re

# --------------------------------------------------------------------------
# Generador
# --------------------------------------------------------------------------

_CURSOS = [
    # (id, título, icono, color, nº temas)
    (1, "Matemáticas", "📐", "#4f46e5", 3),
    (2, "Historia", "📜", "#b45309", 2),
    (3, "Inglés", "🌍", "#047857", 2),
    (4, "Programación", "💻", "#7c3aed", 2),
]

# ids que los storyboards esperan tras el remapeo (ver AGENTS/README)
#   cursos 1 y 2 · temas 11 y 21 · bloques 101 (audio/editor) y 201 (PDF)
_TEMAS = {
    1: [(11, "Tema 1 — Ecuaciones", 101, "content"),
        (12, "Tema 2 — Geometría", 102, "content"),
        (13, "Tema 3 — Álgebra", 103, "content")],
    2: [(21, "Tema 1 — La Prehistoria", 201, "pdf-ref"),
        (22, "Tema 2 — Roma", 202, "content")],
    3: [(31, "Unit 1 — Presentations", 301, "content"),
        (32, "Unit 2 — Everyday English", 302, "content")],
    4: [(41, "Bloque 1 — Variables", 401, "content"),
        (42, "Bloque 2 — Funciones", 402, "content")],
}

_HABITS = ["Hábito 1", "Hábito 2", "Hábito 3", "Hábito 4"]

_QUICK_NOTE = "- Revisar el tema 5\n- Resolver los ejercicios de la semana\n"

# Correo de mentira: mismo dominio que un ejemplo público, nunca el real.
_EMAIL_FALSO = "usuario@ejemplo.com"


def _ahora() -> str:
    return "2026-01-01T00:00:00.000000+00:00"


def _bloque(bid: int, cid: int, tid: int, titulo: str, tipo: str) -> dict:
    """Un bloque. El PDF apunta a un archivo de mentira servido por Flask."""
    # El tipo real en courses-blocks.js es "pdf-ref", no "pdf": con "pdf"
    # el frontend cae en su rama de "Tipo no soportado" y no monta el visor.
    url = "/api/pdf/201" if tipo == "pdf-ref" else None
    return {
        "id": bid, "course_id": cid, "topic_id": tid, "title": titulo,
        "type": tipo, "content": "Contenido de ejemplo para el tutorial.",
        "url": url, "image_id": None, "color": "", "collapsed": False,
        "done": False, "order_index": 0,
        "created_at": _ahora(), "updated_at": _ahora(), "user_id": 1,
    }


def courses() -> dict:
    """Réplica de /api/courses con la forma exacta que espera el frontend."""
    out = []
    for cid, titulo, icono, color, _ in _CURSOS:
        temas, orden = [], 0
        for tid, ttitulo, bid, btipo in _TEMAS[cid]:
            bloques = [_bloque(bid, cid, tid, "Apuntes del tema", btipo)]
            temas.append({
                "id": tid, "course_id": cid, "title": ttitulo, "description": "",
                "notes": "", "status": "pendiente", "order_index": orden,
                "actual_minutes": 0, "estimated_minutes": 60,
                "blocks": bloques, "user_id": 1,
                "created_at": _ahora(), "updated_at": _ahora(),
            })
            orden += 1
        out.append({
            "id": cid, "title": titulo, "description": "", "icon": icono,
            "color": color, "is_favorite": False, "progress": 0,
            "order_index": cid, "blocks": [], "topics": temas, "user_id": 1,
            "created_at": _ahora(), "updated_at": _ahora(),
        })
    return {"courses": out}


def topic_blocks(course_id: int, topic_id: int) -> dict:
    """GET /api/courses/<c>/topics/<t>/blocks → {blocks:[...]}.

    El pencil de editar no usa los datos que ya tiene la tarjeta: vuelve a
    pedirlos con listTopicBlocks y busca el bloque por id. Sin esta ruta el
    pencil no encuentra el bloque 101, avisa por consola y no abre el
    editor: el vídeo se queda con un cursor sobre un botón muerto.
    """
    for cid, temas in _TEMAS.items():
        if cid != course_id:
            continue
        for tid, ttitulo, bid, btipo in temas:
            if tid == topic_id:
                return {"blocks": [_bloque(bid, cid, tid, "Apuntes del tema", btipo)]}
    return {"blocks": []}


def course_detail(course_id: int) -> dict:
    """Réplica de GET /api/courses/<id>, que es lo que pinta la portada.

    Ojo: no es la misma ruta que /api/courses. La portada del curso pide el
    detalle aparte (courses.js → fetchCourseDetail) y si esa segunda
    respuesta no se sustituye, el árbol lateral sale con datos falsos pero
    la portada central sale con los reales. Hay que cubrir las dos.
    """
    for cid, titulo, icono, color, _ in _CURSOS:
        if cid != course_id:
            continue
        temas = []
        for orden, (tid, ttitulo, bid, btipo) in enumerate(_TEMAS[cid]):
            temas.append({
                "id": tid, "course_id": cid, "title": ttitulo, "description": "",
                "notes": "", "status": "pendiente", "order_index": orden,
                "actual_minutes": 0, "estimated_minutes": 60,
                "blocks": [_bloque(bid, cid, tid, "Apuntes del tema", btipo)],
                "user_id": 1, "created_at": _ahora(), "updated_at": _ahora(),
            })
        # Envuelto en "course": courses.js lee data.course, no data directo.
        # Sin la envoltura el frontend cae al {topics: []} de su catch y
        # pinta el empty-state en vez de la portada.
        return {"course": {
            "id": cid, "title": titulo, "description": "", "icon": icono,
            "color": color, "is_favorite": False, "progress": 0,
            "order_index": cid, "blocks": [], "topics": temas, "user_id": 1,
            "created_at": _ahora(), "updated_at": _ahora(),
        }}
    return {"course": {"id": course_id, "title": "Curso", "topics": [], "blocks": []}}


def quick_note() -> dict:
    return {"content": _QUICK_NOTE}


def habits_columns() -> dict:
    cols = [{"key": f"h{i}", "label": f"{h}", "note": "", "order": i,
             "type": "checkbox"} for i, h in enumerate(_HABITS, 1)]
    return {"columns": cols}


def habits_grid(days: int = 21) -> dict:
    return {"cells": [], "days": days}


def habits_week() -> dict:
    return {"days": [], "columns": habits_columns()["columns"]}


def quiz_stats(**_: object) -> dict:
    """Contadores a cero: es lo que ve un usuario recién instalado, y así el
    tutorial enseña el estado inicial en vez de tu historial real."""
    return {"accuracy": 0, "by_block": [], "by_topic": [], "correct": 0,
            "ko": 0, "open_errors": 0, "total_answered": 0}


def quiz_questions() -> dict:
    """Preguntas inventadas: el enunciado es contenido real de tu banco."""
    def q(qid, bid, cid, text, opts, ok):
        return {"id": qid, "block_id": bid, "course_id": cid,
                "difficulty": "medium", "text": text, "options": opts,
                "correct_index": ok, "topic_id": None, "explanation": "",
                "created_at": _ahora()}
    return {"questions": [
        q(1, 401, 4, "¿Cuál es el resultado de 2 + 2?",
          ["3", "4", "5", "6"], 1),
        q(2, 402, 4, "¿Qué palabra es un sinonimo de 'rápido'?",
          ["Lento", "Veloz", "Pesado", "Corto"], 1),
    ]}


def notebooklm() -> dict:
    """Aquí vive tu correo de verdad (active_profile, cookie_path). Falso."""
    return {
        "active_profile": _EMAIL_FALSO,
        "connected": True,
        "cookie_path": f"/srv/backend/uploads/notebooklm/profiles/{_EMAIL_FALSO}/storage_state.json",
        "profiles": [{
            "email": _EMAIL_FALSO, "label": "Ejemplo", "connected": True,
            "updated_at": _ahora(),
        }],
        "user_notebooklm_profile": {"email": _EMAIL_FALSO, "connected": True},
        "user_id": 1,
    }


def openzen() -> dict:
    """has_api_key va en true para que el vídeo muestre la misma pantalla,
    pero no se envía ninguna clave: solo el booleano."""
    return {
        "has_api_key": True, "is_default": False, "model": "qwen2.5:7b",
        "server_url": "http://localhost:11434", "updated_at": _ahora(),
    }


def scorm() -> dict:
    """Tu correo del campus lived aquí. Falso."""
    return {
        "has_password": True, "username": "usuario@ejemplo.com",
        "updated_at": _ahora(),
    }


def pdf_import_status() -> dict:
    """Los tres flags en true: es lo que el tutorial enseña a configurar."""
    return {"moodle_scraping": True, "pdf": True, "scorm_zip": True}


def agenda_month(**_: object) -> dict:
    """Sesión única con un título inocuo, nada de tu agenda real."""
    return {"sessions": [{
        "id": 9001, "title": "Repasar apuntes", "notes": "Repasar el tema 5.",
        "date": "2026-01-15", "start_time": "10:00", "end_time": "11:00",
        "calendar_id": 1, "calendar_name": "Personal", "state": "pending",
        "category": None, "user_id": 1,
        "created_at": _ahora(), "updated_at": _ahora(),
    }], "unscheduled": []}


def calendar_status(**_: object) -> dict:
    """Sin identificadores de móvil, sin nombres de calendario reales."""
    return {
        "last_run": {"calendars_ok": 1, "calendars_bad": 0, "skipped": 0,
                     "new_sessions": 0, "updated": 0, "detail": None,
                     "outcome": "ok", "started_at": _ahora(),
                     "finished_at": _ahora(), "updated_at": _ahora()},
        "calendars": [{"id": 1, "name": "Personal", "enabled": True,
                       "colour": "#4f46e5", "url_masked": "https://ejemplo.com/feed.ics"}],
        "phone": {"e164": "+34600000000", "name": "Example"},
        "email": _EMAIL_FALSO,
    }


PDF_DEMO = pathlib.Path(__file__).resolve().parent / "assets" / "demo.pdf"
"""PDF de una página con texto de relleno. No lo genero al vuelo: el visor
lo pide por HTTP y hacerlo aquí añadiría una dependencia (reportlab o
similar) que el proyecto no tiene. PyMuPDF ya está en el stack y lo escribe
en un momento, pero el archivo generado es más fácil de auditar que un
constructor de PDF dentro del pipeline."""


# Rutas → generador. El orden importa: las más específicas primero.
ROUTES: list[tuple[str, object]] = [
    (r"/api/courses",                    courses),
    (r"/api/quick-note",                 quick_note),
    (r"/api/habits/columns",             habits_columns),
    (r"/api/habits/grid",                habits_grid),
    (r"/api/habits/week",                habits_week),
    (r"/api/agenda/(month|week)",        agenda_month),
    (r"/api/calendar/status",            calendar_status),
    (r"/api/calendar/notifications/.*",  calendar_status),
    # Los ajustes guardan correos reales: activo_profile, cookie_path,
    # username del campus. Van aquí porque el tutorial los enseña.
    (r"/api/settings/notebooklm/.*",     notebooklm),
    (r"/api/settings/openzen-credentials", openzen),
    (r"/api/settings/scorm-credentials", scorm),
    (r"/api/pdf/import/status",          pdf_import_status),
    (r"/api/quiz/stats",                 quiz_stats),
    (r"/api/quiz/questions",             quiz_questions),
]


def respuesta(path: str) -> object | None:
    """Devuelve la respuesta falsa para *path*, o None si no intervenimos.

    Un None significa "deja pasar la petición real": es lo que mantiene
    estables /api/health y cualquier otro endpoint que no contiene datos
    personales y cuya presencia los storyboards dan por hecha.
    """
    # /api/courses/<c>/topics/<t>/blocks — el orden importa: si se comprobara
    # /api/courses/<id> primero, el 1 de "topics" se leería como id de curso.
    mb = re.search(r"/api/courses/(\d+)/topics/(\d+)/blocks", path)
    if mb:
        return topic_blocks(int(mb.group(1)), int(mb.group(2)))
    # /api/courses/<id> — el detalle, que va antes que la lista en ROUTES
    # para que no lo capture la entrada genérica de /api/courses.
    m = re.search(r"/api/courses/(\d+)", path)
    if m:
        return course_detail(int(m.group(1)))
    for patron, gen in ROUTES:
        if re.search(patron, path):
            return gen()
    return None


def respuestas() -> dict[str, object]:
    """Volcado legible para depurar a mano."""
    return {
        "/api/courses":       courses(),
        "/api/quick-note":    quick_note(),
        "/api/habits/columns": habits_columns(),
        "/api/agenda/month":  agenda_month(),
        "/api/calendar/status": calendar_status(),
    }


if __name__ == "__main__":
    print(json.dumps(respuestas(), ensure_ascii=False, indent=2))