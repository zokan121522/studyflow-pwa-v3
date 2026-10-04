"""Schema for image blocks.

Two pieces, both idempotent, so an existing install upgrades on boot
without a migration tool (this repo has none — see `_migrate_topic_notes`).

The link lives on `blocks` rather than the other way round. Two reasons,
both about the shape of the existing code:

* `list_topic_blocks` does a plain `SELECT * FROM blocks`, so a column
  added here arrives in the API payload with no query change and no
  per-block request. The alternative (`images.block_id`, the direction
  `quiz_questions` uses) would mean one lookup request per image block.
* `Block.from_row` reads optional fields with `row.get()`, so an absent
  column on an older row is a `None`, not a `KeyError`.
"""
from __future__ import annotations


def create_images_tables(cur) -> None:
    """Create `images` and give `blocks` its link to it. Safe to re-run."""
    # Declared before the ALTER below so the foreign key target exists.
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS images (
            id SERIAL PRIMARY KEY,
            user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
            course_id INTEGER REFERENCES courses(id) ON DELETE SET NULL,
            topic_id INTEGER REFERENCES topics(id) ON DELETE SET NULL,
            filename VARCHAR(255) NOT NULL,
            original_name VARCHAR(255) NOT NULL,
            mime VARCHAR(32) NOT NULL,
            file_size BIGINT,
            storage_path VARCHAR(500),
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        )
        """
    )
    # ON DELETE SET NULL, not CASCADE: deleting an image must leave the
    # block standing. The render then falls back to its "missing image"
    # state and the title stays editable — same grace the pdf-ref block
    # gets when its document is gone.
    cur.execute(
        "ALTER TABLE blocks ADD COLUMN IF NOT EXISTS image_id INTEGER "
        "REFERENCES images(id) ON DELETE SET NULL"
    )