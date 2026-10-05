# launcher/serve.py
"""The child process behind the StudyFlow launcher.

Kept separate from launch.py so that nothing in the serving path can ever
re-enter the single-instance, port-picking or browser-opening logic. This
module starts the app and then blocks; that is the whole job.

Two things here are deliberately different from ``backend/server.py``'s
``__main__``, which is still there for the hosted v3 deployment:

* **Binds 127.0.0.1, not 0.0.0.0.** The hosted app sits behind a tunnel and
  needs to be reachable from the network; a local install must not be. On a
  laptop joined to a café Wi-Fi, 0.0.0.0 means every device in the room can
  read the user's agenda and write to their database.
* **debug=False, no reloader.** The reloader spawns a child process, which
  would fight the launcher for the pidfile and leave an orphan behind on
  every relaunch.
"""

from __future__ import annotations

import os
import sys


def _repo_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    port = int(os.environ.get("STUDYFLOW_PORT", "8477"))

    root = _repo_root()
    # backend/ first so server.py's flat imports (import engine, not
    # import backend.engine) resolve. Same ordering as scripts/dev-server.sh.
    sys.path.insert(0, os.path.join(root, "backend"))
    sys.path.insert(0, root)

    import server  # noqa: E402  (import after sys.path is set up)

    app = server.create_app()
    # threaded=True is safe now: database.get_connection() hands out one
    # connection per thread. It was not before, and the shared handle
    # deadlocked under load. See the commit on backend/database.py.
    app.run(
        host="127.0.0.1",
        port=port,
        debug=False,
        threaded=True,
        use_reloader=False,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())