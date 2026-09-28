"""
Public markdown template catalog for NotebookLM prompts (phase 7.7).

Port of v2 ``backend.ai.generation.prompts.MD_TEMPLATES``*UI metadata* —
the modal grid (name/emoji/description) and the live preview ``mock``.
Prompt *builders* stay in ``md_templates.py`` (``_ROLES_BY_ID``) and are
referenced by id, so this module is pure metadata:

    GET /api/ai/md-templates  →  {"templates": [{id, name, emoji,
                                                 description, mock}, ...]}

The mock is a short illustrative sample of the expected output shape —
the frontend renders it with the markdown block renderer for the
live preview inside the config modal.
"""


# ─── Public catalog ────────────────────────────────────────────

MD_TEMPLATES: dict[str, dict] = {
    "notas-estandar": {
        "name": "Notas estándar",
        "emoji": "📝",
        "description": "Teoría general — callouts de color, títulos con emoji y negritas.",
        "mock": (
            "## 📖 Definición\n"
            "La **computación en la nube** ofrece recursos informáticos bajo demanda por internet.\n\n"
            "> [!info] **Concepto clave**\n"
            "> Escalado automático: la capacidad crece o se reduce según el tráfico real.\n\n"
            "## 🔗 Conceptos clave\n"
            "- **Escalabilidad** — ajustar recursos según necesidad\n"
            "- **Virtualización** — abstraer el hardware físico\n\n"
            "> [!tip] **Consejo**\n"
            "> Mide el *lock-in* del proveedor antes de elegir plataforma.\n\n"
            "## 🚀 Takeaways\n"
            "- ✅ La nube = recursos bajo demanda y pago por uso\n"
        ),
    },
    "transcripcion": {
        "name": "Reconstrucción de transcripción",
        "emoji": "🧠",
        "description": "Transcripciones ruidosas (ASR/OCR) — reconstruye, corrige y organiza.",
        "mock": (
            "## 🧠 Transcripción reconstruida\n"
            "> [!info] **Interpretación**\n"
            "> \"la mikrosirvicetos son arquitetura ke divide la aplikasion\" → Los "
            "**microservicios** son una arquitectura que divide la aplicación en servicios independientes.\n\n"
            "## ⚠️ Errores ASR corregidos\n"
            "- mikrosirvicetos → **microservicios**\n"
            "- aplikasion → **aplicación**\n\n"
            "## 📌 Idea principal\n"
            "Los microservicios permiten desplegar cada servicio de forma aislada.\n"
        ),
    },
    "tutorial": {
        "name": "Tutorial / How-To",
        "emoji": "⚙️",
        "description": "Guías prácticas — pasos numerados, comandos y errores comunes.",
        "mock": (
            "## ⚙️ Cómo crear un contenedor Docker\n"
            "> [!tip] **Requisitos**\n"
            "> Docker instalado y el demonio en ejecución.\n\n"
            "1. Crea el `Dockerfile` en la raíz\n"
            "2. Construye la imagen: `docker build -t mi-app .`\n"
            "3. Ejecuta: `docker run -p 8080:80 mi-app`\n\n"
            "> [!warning] **Error común**\n"
            "> Olvidar `-p` hace que el puerto no sea accesible desde el host.\n\n"
            "## ✅ Verificación\n"
            "Abre `http://localhost:8080` y confirma que la app responde.\n"
        ),
    },
    "comparativa": {
        "name": "Comparativa",
        "emoji": "🔀",
        "description": "Contenido que compara opciones — tablas y recomendación final.",
        "mock": (
            "## 🔀 Flask vs FastAPI\n\n"
            "| Criterio | Flask | FastAPI |\n"
            "|----------|-------|---------|\n"
            "| Rendimiento | Medio | Alto |\n"
            "| Validación | Manual | Automática (Pydantic) |\n"
            "| Docs | Sin auto | OpenAPI |\n\n"
            "> [!info] **Veredicto**\n"
            "> Flask para prototipos; FastAPI para APIs con validación estricta y rendimiento.\n"
        ),
    },
    "glosario": {
        "name": "Glosario / Términos",
        "emoji": "📚",
        "description": "Definiciones compactas, siglas y términos relacionados.",
        "mock": (
            "## 📚 Glosario — Términos clave\n"
            "> [!example] **Formato**\n"
            "> Definiciones compactas, una por término, en orden alfabético.\n\n"
            "- **API** — Interfaz de programación de aplicaciones.\n"
            "- **ORM** — Mapeo objeto-relacional entre código y base de datos.\n\n"
            "| Sigla | Significado |\n"
            "|-------|-------------|\n"
            "| API | Application Programming Interface |\n"
            "| ORM | Object-Relational Mapping |\n\n"
            "> [!info] **Términos relacionados**\n"
            "> API ↔ SDK: el SDK envuelve la API con utilidades de alto nivel.\n"
        ),
    },
    "resumen-ejecutivo": {
        "name": "Resumen ejecutivo",
        "emoji": "🎯",
        "description": "Markdown largo → síntesis breve con bullets y cifras clave.",
        "mock": (
            "## 🎯 Resumen ejecutivo\n"
            "> [!info] **En una frase**\n"
            "> La automatización de tests reduce hasta un 40 % los bugs en producción.\n\n"
            "- ✅ **CI/CD** — integración en cada commit\n"
            "- ✅ **Cobertura** — mínimo 80 % en módulos críticos\n"
            "- ✅ **Feedback** — alertas tempranas al equipo\n\n"
            "> [!warning] **Riesgo**\n"
            "> La cobertura alta no garantiza calidad si los tests son superficiales.\n"
        ),
    },
    "por-temas": {
        "name": "Por temas",
        "emoji": "🧩",
        "description": "Reorganiza en secciones temáticas mutuamente excluyentes.",
        "mock": (
            "## 🧩 Tema 1: Fundamentos\n"
            "> [!info] **Ideas clave**\n"
            "> La base del tema: definiciones y principios esenciales.\n\n"
            "## 🧩 Tema 2: Aplicaciones\n"
            "> [!example] **Casos de uso**\n"
            "> Cómo se aplica en proyectos reales.\n\n"
            "## 🧩 Tema 3: Herramientas\n"
            "> [!tip] **Stack recomendado**\n"
            "> Las librerías y comandos que aparecen en el material.\n"
        ),
    },
    "faq": {
        "name": "FAQ",
        "emoji": "❓",
        "description": "Convierte el contenido en preguntas y respuestas claras.",
        "mock": (
            "## ❓ Preguntas frecuentes\n\n"
            "**¿Qué es X?**\n"
            "> [!info] **Respuesta**\n"
            "> Definición breve y clara.\n\n"
            "**¿Cómo se instala?**\n"
            "> [!tip] **Guía rápida**\n"
            "> Los tres comandos esenciales para empezar.\n\n"
            "**¿Cuándo NO usarlo?**\n"
            "> [!warning] **Límites**\n"
            "> Casos donde la herramienta no es la opción adecuada.\n"
        ),
    },
    "arquitectura-tecnica": {
        "name": "Arquitectura técnica",
        "emoji": "🏗️",
        "description": "Docs técnicas — componentes, flujos, diagramas ASCII y riesgos.",
        "mock": (
            "## 🏗️ Arquitectura del sistema\n"
            "> [!info] **Componentes**\n"
            "> Cliente SPA → API Flask → PostgreSQL.\n\n"
            "```\n"
            "Cliente → [Nginx] → [Flask API] → [PostgreSQL]\n"
            "                └────────────→ [Redis cache]\n"
            "```\n\n"
            "## 🔄 Flujo de datos\n"
            "1. El cliente autentica con JWT\n"
            "2. La API valida y consulta la BD\n"
            "3. Se cachean respuestas frecuentes\n\n"
            "> [!warning] **Riesgos**\n"
            "> Latencia N+1 y consultas sin índice en tablas grandes.\n"
        ),
    },
    "infografia-textual": {
        "name": "Infografía textual",
        "emoji": "📊",
        "description": "Máxima riqueza visual — callouts densos, tablas y ritmo visual.",
        "mock": (
            "## 📊 Infografía — En cifras\n"
            "> [!info] **Dato clave**\n"
            "> 8 de cada 10 equipos usan CI/CD en 2026.\n\n"
            "| Métrica | Valor |\n"
            "|---------|-------|\n"
            "| Deploy semanales | 12 |\n"
            "| MTTR | 18 min |\n"
            "| Cobertura | 84 % |\n\n"
            "> [!tip] **Best practice**\n"
            "> Pipelines cortos y feedback inmediato al desarrollador.\n"
        ),
    },
}


# ─── Public API ────────────────────────────────────────────────

def list_md_templates() -> list[dict]:
    """Public metadata for the frontend config modal (NO internal prompts)."""
    return [
        {
            "id": tpl_id,
            "name": data["name"],
            "emoji": data["emoji"],
            "description": data["description"],
            "mock": data["mock"],
        }
        for tpl_id, data in MD_TEMPLATES.items()
    ]