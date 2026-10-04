"""Image blocks (R4).

Split out of `backend/` proper so the DDL does not grow `database.py`,
which is already over the size limit and refuses new table definitions
(same precedent as `calendar_import`).
"""