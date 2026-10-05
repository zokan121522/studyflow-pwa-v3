"""Regresión: los módulos de backup no pueden hardcodear /data.

Bug real encontrado probando la app en el Mac del usuario (2026-10-05):
"❌ Error al restaurar: Restore failed: [Errno 30] Read-only file system:
'/data'". Los módulos de backup resolvían el directorio de datos con
os.environ.get("DATA_DIR", "/data"), el convenio de Docker. El launcher
exporta STUDYFLOW_DATA_DIR y PDF_UPLOAD_FOLDER, pero nunca DATA_DIR, así
que en local caía al default "/data" — y la raíz del filesystem está en
solo lectura bajo SIP, de modo que el restore no podía escribir nada.

engine.data_dir() ya existía y resolvía bien en macOS, XDG y Windows.
 Estos módulos simplemente no lo usaban.
"""

import importlib
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "backend"), str(ROOT)]

MODULES = ["backup_user_restore", "backup_user", "backup_options"]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Sin DATA_DIR ni STUDYFLOW_DATA_DIR: reproduce el arranque real."""
    monkeypatch.delenv("DATA_DIR", raising=False)
    monkeypatch.delenv("STUDYFLOW_DATA_DIR", raising=False)
    yield


@pytest.mark.parametrize("module_name", MODULES)
def test_no_default_hardcoded_slash_data(module_name, monkeypatch):
    """El código no debe volver a traer "/data" como default."""
    src = (ROOT / "backend" / f"{module_name}.py").read_text()
    assert 'os.environ.get("DATA_DIR", "/data")' not in src, (
        f"{module_name}: vuelve el default duro '/data'; usa engine.data_dir()"
    )


@pytest.mark.parametrize("module_name", MODULES)
def test_data_dir_resolves_to_a_writable_local_path(module_name):
    """Sin DATA_DIR, debe resolverse al directorio de datos real y escribible."""
    mod = importlib.import_module(module_name)
    data_dir = mod.DATA_DIR
    assert data_dir, f"{module_name}: DATA_DIR vacío"
    assert data_dir != "/data", (
        f"{module_name}: DATA_DIR cayó en /data (solo válido en Docker)"
    )
    assert os.path.isdir(data_dir), f"{module_name}: {data_dir} no existe"
    assert os.access(data_dir, os.W_OK), f"{module_name}: {data_dir} no es escribible"


@pytest.mark.parametrize("module_name", MODULES)
def test_docker_env_var_still_wins(module_name, monkeypatch):
    """El despliegue en contenedor sí debe poder fijar DATA_DIR=/data."""
    monkeypatch.setenv("DATA_DIR", "/data")
    mod = importlib.import_module(module_name)
    importlib.reload(mod)
    assert mod.DATA_DIR == "/data", "DATA_DIR explícito debe mandar en Docker"
    monkeypatch.delenv("DATA_DIR")
    importlib.reload(mod)


@pytest.mark.parametrize("module_name", MODULES)
def test_data_dir_env_override_is_honoured(module_name, monkeypatch, tmp_path):
    """STUDYFLOW_DATA_DIR (el launcher) debe respetarse."""
    target = tmp_path / "studyflow-test"
    monkeypatch.setenv("STUDYFLOW_DATA_DIR", str(target))
    mod = importlib.import_module(module_name)
    importlib.reload(mod)
    assert mod.DATA_DIR == str(target), (
        f"{module_name}: ignora STUDYFLOW_DATA_DIR, que es lo que exporta el launcher"
    )
    monkeypatch.delenv("STUDYFLOW_DATA_DIR")
    importlib.reload(mod)


def test_restore_path_stays_inside_data_dir():
    """La protección zip-slip debe seguirوافق con el DATA_DIR resuelto."""
    mod = importlib.import_module("backup_user_restore")
    assert mod._has_unsafe_member(["../etc/passwd"])
    assert mod._has_unsafe_member(["/etc/passwd"])
    assert not mod._has_unsafe_member(["uploads/pdfs/ok.pdf"])