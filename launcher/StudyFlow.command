#!/bin/bash
# StudyFlow (macOS) — doble clic en este fichero.
# Terminal abre, y aqui solo pasamos el testigo al lanzador de al lado
# (argumentos incluidos: ./StudyFlow.command stop  →  StudyFlow.sh stop).
exec bash "$(dirname "$0")/StudyFlow.sh" "$@"
