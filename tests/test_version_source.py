"""The app version has a single source of truth: backend/routes/health.py.

The three portable build scripts extract ``APP_VERSION`` from there. These
tests run the very block the scripts run, so a silent fallback (the old
``'3.0.0'`` default) can never creep back in unnoticed. Fast and offline.
"""

import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
HEALTH = REPO / 'backend/routes/health.py'
BUILD_SCRIPTS = (
    'scripts/build-windows-portable.sh',
    'scripts/build-macos-portable.sh',
    'scripts/build-linux-portable.sh',
)
APP_VERSION_RE = re.compile(r"APP_VERSION\s*=\s*['\"]([^'\"]+)")


def app_version():
    match = APP_VERSION_RE.search(HEALTH.read_text())
    assert match, f'APP_VERSION not found in {HEALTH}'
    return match.group(1)


def version_block(script_name):
    """Return the ``VERSION="$(...)`` assignment exactly as the script has it."""
    lines = (REPO / script_name).read_text().splitlines(keepends=True)
    block, started = [], False
    for line in lines:
        if line.startswith('VERSION='):
            started = True
        if started:
            block.append(line)
            if line.rstrip('\n').endswith('")"'):
                return ''.join(block)
    raise AssertionError(f'no VERSION block found in {script_name}')


def run_block(script_name, cwd):
    script = version_block(script_name)
    wrapped = 'set -euo pipefail\n' + script + '\nprintf "%s" "$VERSION"\n'
    result = subprocess.run(
        ['bash', '-c', wrapped],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=30,
    )
    return result


def test_scripts_resolve_the_health_py_version():
    expected = app_version()
    for script in BUILD_SCRIPTS:
        result = run_block(script, REPO)
        assert result.returncode == 0, f'{script}: {result.stderr.strip()}'
        assert result.stdout.strip() == expected, (
            f'{script} resolved {result.stdout.strip()!r}, '
            f'health.py says {expected!r}'
        )


def test_missing_version_fails_loudly(tmp_path):
    """No APP_VERSION -> the script must exit non-zero, never a default."""
    for script in BUILD_SCRIPTS:
        result = run_block(script, tmp_path)
        assert result.returncode != 0, f'{script} silently fell back'
        assert 'APP_VERSION not found' in result.stderr


def test_scripts_carry_no_hardcoded_fallback():
    for script in BUILD_SCRIPTS:
        text = (REPO / script).read_text()
        assert '3.0.0' not in text, f'{script} hardcodes 3.0.0'
        assert not re.search(r'echo\s+"3\.\d+\.\d+"', text), (
            f'{script} keeps an echo fallback for the version'
        )
        assert '2>/dev/null || echo' not in text, (
            f'{script} swallows the extraction error'
        )


def test_app_version_is_semver():
    assert re.fullmatch(r'\d+\.\d+\.\d+', app_version())
