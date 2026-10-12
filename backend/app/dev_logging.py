"""Local-development log policy, used only by `python run.py` via dev_logging.json.

Production (plain `uvicorn app.main:app`) never loads this module, so its logging
is unchanged. The policy:

- root at WARNING: every third-party library is quiet unless something is wrong,
  with no per-library list to maintain;
- `app.*` at INFO: TarifeAI's own messages stay visible;
- uvicorn access lines shortened and redacted; its startup chatter at WARNING.

Nothing here adds information: it only shortens, redacts and relabels.
"""

import logging


class AccessLine(logging.Filter):
    """Reduce uvicorn's access line to `METHOD /path status`.

    The query string and client address are dropped: queries can carry user text,
    and the address identifies the user. Headers are never part of the record.
    """

    def filter(self, record):
        try:
            _client, method, path, _version, status = record.args
        except (TypeError, ValueError):
            return True
        record.msg = "%s %s %s"
        record.args = (method, str(path).split("?", 1)[0], status)
        return True


class Milestones(logging.Filter):
    """Turn the embedding warm-up into one readiness line.

    Warm-up is the first real encode (it includes the load), so its message is the
    moment the model can actually answer; the earlier "loaded" step is folded into it.
    Failures are WARNING/ERROR records and pass through untouched.
    """

    def filter(self, record):
        if record.name != "app.rag.embed" or record.levelno != logging.INFO or not isinstance(record.msg, str):
            return True
        if record.msg.startswith("embedding model loaded"):
            return False
        if record.msg.startswith("embedding model warm in"):
            record.msg = "✓ Embedding ready (%.1fs)"
            record.milestone = True
        return True


class DevFormatter(logging.Formatter):
    """Milestones print bare, like the launcher's own ready lines."""

    def format(self, record):
        if getattr(record, "milestone", False):
            return f"  {record.getMessage()}"
        return super().format(record)
