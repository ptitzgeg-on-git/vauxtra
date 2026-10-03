"""Per-row outcome reporting shared by the two import routes (services and Docker).

A refused row and a row skipped on purpose are reported differently; keeping the helpers
here keeps both routes consistent.
"""

from app.models import add_log


def refuse_import(errors: list, conn, item: str, reason: str) -> None:
    """Report a row the import refused: appended to `errors` and logged.

    For rows that are wrong and need fixing. Rows skipped on purpose use `set_aside`.
    """
    message = f"Import skipped {item}: {reason}"
    errors.append(message)
    add_log("warning", message, conn)


def set_aside(skipped: list, item: str, reason: str) -> None:
    """Report a row skipped on purpose (e.g. already tracked) in `skipped`. Not logged:
    a re-import would otherwise write one line per tracked row.
    """
    skipped.append(f"Import passed over {item}: {reason}")
