"""calendar_import — automatic Digitech/Moodle calendar import.

Split out of routes/calendar.py during Phase 9 so the scheduling, window
and notification concerns do not live inside an HTTP route module that had
already reached the size limit.
"""

__all__ = ["window", "notifications", "importer", "scheduler", "schema", "routes"]