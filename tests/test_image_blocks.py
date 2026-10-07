"""R4 — image blocks: upload, serve, delete, and the wiring that reaches them.

An `image` block stores no bytes. It holds an `image_id` pointing at a row in
`images`, and the pixels come off the uploads volume. That indirection is where
the bugs live, so the tests concentrate on it:

* The upload accepts whatever the browser sends, and the browser is not a
  trust boundary. A file named `.png` whose payload is HTML is a stored-XSS
  primitive the instant something sniffs the response — hence the magic-byte
  check, asserted here against the cases that actually occur in the wild.
* Deleting the block has to take the image with it. `blocks.image_id` is
  `ON DELETE SET NULL`, so the FK quietly preserves an orphan row and a file
  that no UI path can ever reach again.
* A bare `<img src>` carries no Authorization header. That only works because
  `token_required` falls back to the local user, and the response must be
  pinned as `nosniff` so the declared `image/*` is not second-guessed.
"""

import io
import os
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest
import re
from flask import Flask

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from routes.image import bp as image_bp  # noqa: E402
from routes.auth import generate_token  # noqa: E402

BLOCKS_JS = ROOT / "frontend/features/studyflow/courses-blocks.js"
UPLOAD_JS = ROOT / "frontend/features/studyflow/image-upload.js"
SW_JS = ROOT / "frontend/sw.js"
INDEX_HTML = ROOT / "frontend/index.html"
BLOCKS_PY = ROOT / "backend/routes/blocks.py"
SCHEMA_PY = ROOT / "backend/images/schema.py"
COMPOSE = ROOT / "docker-compose.yml"

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + b"\x00" * 20
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 20
# What an attacker actually uploads: a real HTML document called .png.
FAKE_PNG = b"<!DOCTYPE html><script>alert(document.cookie)</script>"


# ============================== Pure helpers ==============================
def test_sniff_accepts_real_png_and_jpeg():
    from routes.image import _sniff_image

    assert _sniff_image(PNG) == ("png", "image/png")
    assert _sniff_image(JPEG) == ("jpg", "image/jpeg")


@pytest.mark.parametrize(
    "payload, why",
    [
        (FAKE_PNG, "HTML wearing a .png extension is the stored-XSS case"),
        (b"<svg xmlns='http://www.w3.org/2000/svg' onload='alert(1)'/>", "SVG is a script container"),
        (b"%PDF-1.7", "a PDF is not an image"),
        (b"GIF89a", "GIF is out of scope, so it must be refused not guessed"),
        (b"", "empty input"),
    ],
)
def test_sniff_refuses_everything_that_is_not_a_supported_raster(payload, why):
    from routes.image import _sniff_image

    assert _sniff_image(payload) is None, why


def test_title_comes_from_the_filename_without_its_extension():
    """Spec: "the name it was uploaded under becomes the title"."""
    from routes.image import _title_from_filename

    assert _title_from_filename("Diagrama.png") == "Diagrama"
    assert _title_from_filename("Mi foto (2024).jpeg") == "Mi foto (2024)"
    assert _title_from_filename("captura de pantalla.JPG") == "captura de pantalla"


def test_the_title_keeps_the_spaces_that_secure_filename_destroys():
    """Regression pin for a bug this suite actually caught.

    `secure_filename("Diagrama flujo.png")` → `"Diagrama_flujo.png"`. Deriving
    the title from the stored original_name therefore shipped
    "Diagrama_flujo" as the block title, which is not what the user typed and
    not what the spec asks for. The sanitised name exists to build paths; a
    title is not a path.

    The route test below proves the same thing end to end — its stubbed row
    carries original_name="Diagrama.png" while the upload is named
    "Diagrama flujo.png", so the title can only have come from the raw name.
    """
    from routes.image import _title_from_filename
    from werkzeug.utils import secure_filename

    raw = "Diagrama flujo.png"
    assert secure_filename(raw) == "Diagrama_flujo.png", (
        "if this ever stops rewriting spaces, the pin below is still the contract"
    )
    assert _title_from_filename(raw) == "Diagrama flujo"


def test_title_survives_degenerate_filenames():
    """Never return an empty title — the card header would show the fallback."""
    from routes.image import _title_from_filename

    assert _title_from_filename(".png") == "Imagen"  # nothing but an extension
    assert _title_from_filename("   ") == "Imagen"
    assert _title_from_filename("sin_extension") == "sin_extension"


def test_the_15mb_limit_is_the_agreed_one():
    """15 MB, not the PDF route's 50 MB: images here are diagrams."""
    import routes.image as image

    assert image.MAX_FILE_SIZE == 15 * 1024 * 1024


def test_the_js_mirrors_the_server_limit():
    """A front-end guard that drifts from the server is a lie to the user."""
    src = UPLOAD_JS.read_text(encoding="utf-8")
    assert "const MAX_BYTES = 15 * 1024 * 1024;" in src, (
        "image-upload.js limit no longer matches MAX_IMAGE_SIZE in routes/image.py"
    )
    # And the picker must not advertise formats the server would refuse.
    assert "image/svg" not in src
    assert 'ACCEPT_ATTR = "image/png,image/jpeg' in src


# ============================== Routes ==============================
class FakeCursor:
    # execute() returns cur.rowcount and the routes branch on it, so a stub
    # without it raises AttributeError instead of testing anything.
    rowcount = 1

    def __init__(self, rows=()):
        self._rows = list(rows)
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append((" ".join(sql.split()), params))

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeConn:
    def __init__(self, cursor):
        self._c = cursor

    def cursor(self):
        return self._c

    def commit(self):
        pass


def _row(**over):
    base = {
        "id": 5,
        "user_id": 1,
        "course_id": 98,
        "topic_id": 334,
        "filename": "abc123.png",
        "original_name": "Diagrama.png",
        "mime": "image/png",
        "file_size": 2048,
        "storage_path": "/does/not/matter.png",
        "created_at": None,
    }
    base.update(over)
    return base


@pytest.fixture
def client(monkeypatch, tmp_path):
    import database as db
    import routes.image as image

    # Never write to the real uploads folder from a test.
    monkeypatch.setattr(image, "UPLOAD_FOLDER", str(tmp_path / "images"))

    holder = {"cursor": FakeCursor([_row()])}

    # database.execute/fetchone go through get_db(), which owns the pool.
    # Patching get_connection would leave init_db() running for real and blow
    # up on the missing DATABASE_URL.
    @contextmanager
    def fake_get_db():
        yield FakeConn(holder["cursor"])

    monkeypatch.setattr(db, "get_db", fake_get_db)

    app = Flask(__name__)
    app.config["TESTING"] = True
    app.config["JWT_SECRET_KEY"] = "test-secret"
    app.config["JWT_ACCESS_TOKEN_EXPIRES"] = 3600
    app.config["SECRET_KEY"] = "test-secret"
    app.register_blueprint(image_bp, url_prefix="/api")
    return app.test_client(), holder, app, tmp_path


def _auth(app, user_id=1):
    with app.app_context():
        return {"Authorization": f"Bearer {generate_token(user_id)}"}


def _upload(c, app, name="Diagrama.png", payload=PNG, **extra):
    data = {"file": (io.BytesIO(payload), name)}
    data.update(extra)
    return c.post("/api/image/upload", data=data, headers=_auth(app),
                  content_type="multipart/form-data")


def test_upload_rejects_html_disguised_as_a_png(client):
    """The reason the sniff exists. Without it this stores XSS."""
    c, holder, app, _ = client
    r = _upload(c, app, payload=FAKE_PNG)

    assert r.status_code == 400
    assert "PNG" in r.get_json()["error"]
    assert not any("INSERT INTO images" in sql for sql, _ in holder["cursor"].executed), (
        "a rejected upload must not create an images row"
    )


def test_upload_rejects_an_oversized_file_before_sniffing(client):
    c, holder, app, _ = client
    big = PNG + b"\x00" * (16 * 1024 * 1024)
    r = _upload(c, app, payload=big)

    assert r.status_code == 400
    assert "too large" in r.get_json()["error"].lower()


def test_upload_rejects_an_empty_file(client):
    c, holder, app, _ = client
    r = _upload(c, app, payload=b"")

    assert r.status_code == 400


def test_upload_accepts_a_real_png_and_titles_it_after_the_file(client):
    c, holder, app, _ = client
    r = _upload(c, app, name="Diagrama flujo.png")

    assert r.status_code == 201, r.get_json()
    image = r.get_json()["image"]
    assert image["id"] == 5
    assert image["title"] == "Diagrama flujo"
    assert image["file_url"] == "/api/image/5/file"


def test_upload_sends_no_content_type_header_with_the_multipart_body(client):
    """Documented trap: a JSON Content-Type makes Werkzeug drop the file.

    The front-end must not send it. This asserts the server half stays
    compatible with a correctly-labelled FormData.
    """
    c, holder, app, _ = client
    r = _upload(c, app)
    assert r.status_code == 201


def test_upload_refuses_a_course_owned_by_someone_else(client):
    c, holder, app, _ = client
    holder["cursor"] = FakeCursor([])  # _own_course finds nothing
    r = _upload(c, app, course_id="999")

    assert r.status_code == 404


def test_serving_pins_nosniff_and_declares_the_sniffed_mime(client, monkeypatch):
    """A bare <img src> sends no auth header, so the response is the perimeter."""
    import routes.image as image

    c, holder, app, tmp_path = client
    on_disk = tmp_path / "images" / "abc123.png"
    on_disk.parent.mkdir(parents=True, exist_ok=True)
    on_disk.write_bytes(PNG)

    holder["cursor"] = FakeCursor([_row(storage_path=str(on_disk))])
    r = c.get("/api/image/5/file", headers=_auth(app))

    assert r.status_code == 200
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.mimetype == "image/png"
    assert "private" in r.headers.get("Cache-Control", "")


def test_serving_a_row_whose_file_vanished_is_a_404_not_a_500(client):
    """Reachable in practice: the volume is wiped, the DB is not."""
    c, holder, app, _ = client
    holder["cursor"] = FakeCursor([_row(storage_path="/gone/abc123.png")])
    r = c.get("/api/image/5/file", headers=_auth(app))

    assert r.status_code == 404


def test_delete_reaps_the_row_and_the_file(client):
    c, holder, app, tmp_path = client
    on_disk = tmp_path / "images" / "abc123.png"
    on_disk.parent.mkdir(parents=True, exist_ok=True)
    on_disk.write_bytes(PNG)

    holder["cursor"] = FakeCursor([_row(storage_path=str(on_disk))])
    r = c.delete("/api/image/5", headers=_auth(app))

    assert r.status_code == 200
    assert not on_disk.exists(), "the file must not be left on the volume"
    assert any("DELETE FROM images" in sql for sql, _ in holder["cursor"].executed)


def test_deleting_an_image_leaves_the_block_standing(client):
    """ON DELETE SET NULL: the block survives, it just has nothing to show."""
    schema = SCHEMA_PY.read_text(encoding="utf-8")
    assert "ON DELETE SET NULL" in schema, (
        "image_id must be SET NULL so the block outlives a deleted image"
    )
    assert "ADD COLUMN IF NOT EXISTS image_id" in schema, (
        "the ALTER must stay idempotent: init_db runs on every boot"
    )


# ============================== Block wiring ==============================
def test_image_id_is_a_writable_block_field():
    """Without this the create silently drops image_id and the block is blank."""
    src = BLOCKS_PY.read_text(encoding="utf-8")
    assert "'done', 'color', 'order_index', 'image_id'," in src, (
        "image_id missing from _WRITABLE_FIELDS — create_block would ignore it"
    )
    # Both INSERT sites (topic-level and course-level) must carry the column.
    assert src.count("order_index, color, collapsed, done, image_id") == 2


def test_deleting_an_image_block_takes_the_image_with_it():
    """Otherwise every deleted image litters the volume forever.

    The FK is SET NULL, so the database will happily keep the row and the file
    — and no screen in the app can reach either again.
    """
    src = BLOCKS_PY.read_text(encoding="utf-8")
    assert "_reap_image(current_user_id, block['type'], block.get('image_id'))" in src, (
        "delete_block does not reap the image — orphans are permanent"
    )
    assert "if block_type != 'image' or not image_id:" in src, (
        "_reap_image must only fire for image blocks"
    )


def test_deleting_any_other_block_never_touches_images():
    """A markdown block must not be able to trigger image deletion."""
    src = BLOCKS_PY.read_text(encoding="utf-8")
    guard = src.split("def _reap_image")[1]
    assert "block_type != 'image'" in guard.split("return")[0]


# ============================== Front-end wiring ==============================
def test_the_image_block_has_a_renderer():
    src = BLOCKS_JS.read_text(encoding="utf-8")
    assert '} else if (type === "image") {' in src
    assert "/api/image/${imageId}/file" in src, (
        "the render must hit the served-file endpoint"
    )
    # No image_id means the image row is gone; say so instead of a broken icon.
    assert "Imagen no disponible" in src


def test_the_image_chip_uploads_before_it_creates_the_block():
    """The subtle one.

    Every other chip creates the block first and patches it afterwards (see
    the pdf-ref popover). Doing that for `image` would create a block whose
    image_id is null — permanently blank — because TYPE_META defaults carry no
    image. So the image path must branch out BEFORE the generic addBlock.
    """
    src = BLOCKS_JS.read_text(encoding="utf-8")
    branch = src.index('if (type === "image")')
    generic = src.index("await addBlock(cid, {")
    assert branch < generic, (
        "the image interception must come before the generic addBlock call"
    )
    assert "window.App.ImageUpload" in src


def test_image_upload_module_is_loaded_before_the_chip_handler():
    html = INDEX_HTML.read_text(encoding="utf-8")
    assert html.index("/features/studyflow/image-upload.js") < html.index(
        "/features/studyflow/courses-blocks.js"
    ), "the 🖼 chip resolves App.ImageUpload at click time — order matters"


def test_image_upload_is_precached():
    """A chip that 404s offline is worse than no chip."""
    sw = SW_JS.read_text(encoding="utf-8")
    assert "'/features/studyflow/image-upload.js'" in sw
    # The literal version moves with every later feature, so compare the
    # number instead: what matters is that R4's bump was not undone.
    version = re.search(r"const ASSET_CACHE = 'studyflow-assets-v(\d+)'", sw)
    assert version, "ASSET_CACHE not found in sw.js"
    assert int(version.group(1)) >= 87, (
        "R4 bumped the asset cache to v87 for image-upload.js; a lower number "
        "means the bump was reverted and installed PWAs keep the old module"
    )


def test_the_volume_is_declared_and_mounted():
    compose = COMPOSE.read_text(encoding="utf-8")
    assert "IMAGE_UPLOAD_FOLDER: /srv/backend/uploads/images" in compose, (
        "the env var the route reads is missing"
    )
    assert "image_uploads:/srv/backend/uploads/images" in compose, (
        "without the mount the file is lost on every container rebuild"
    )
    assert re_volumes_declared(compose), (
        "image_uploads is mounted but not declared under top-level volumes:"
        " Docker would create it implicitly instead of naming it"
    )


def re_volumes_declared(compose):
    top = compose.split("\nvolumes:")[-1]
    return any(line.strip().startswith("image_uploads:") for line in top.splitlines())