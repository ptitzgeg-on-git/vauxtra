"""What an import did to each row, said the same way by every route that imports.

Vauxtra has two of them. `/api/services/import` takes what a DNS or proxy provider lists,
and `/api/docker/import` takes what a Docker daemon lists. They are the same operation on
two inventories, and they were answering in two different vocabularies: one had learnt to
separate a row it refused from a row it passed over on purpose, name both, and write the
refusals to the journal; the other still counted every non-import into a single `skipped`
integer and logged none of them.

That difference is not cosmetic. `skipped` covering both outcomes means the one case the
operator has to go and fix is reported with the same number, the same colour and the same
word as the case they can ignore -- and with no name attached, there is nothing to go and
fix it *with*. Keeping the two helpers here rather than beside one route is what stops the
next fix from landing on one import and not the other.
"""

from app.models import add_log


def refuse_import(errors: list, conn, item: str, reason: str) -> None:
    """Name a row the import could not take, in the list the callers read and in the journal.

    `errors` is the only list the callers of an import route read as a failure: the setup
    wizard counts it, the settings panel counts it, and `vauxtra_mcp` hands it back as it
    stands. What happened instead was a bare `continue` on four paths of the service import
    and, on a fifth, a dictionary comprehension that dropped a nameless record -- and a
    second record for a name already in the map -- before the loop even started. A name the
    operator had just ticked came back as `{"imported": 0, "errors": []}`, which is also the
    answer for "there was nothing to do", so the wizard showed no banner of either colour
    and stepped to "all set".

    This is for rows that are *wrong*, where the operator has something to go and fix. Rows
    passed over on purpose go to `set_aside`, and the difference is the colour of the banner.
    """
    message = f"Import skipped {item}: {reason}"
    errors.append(message)
    add_log("warning", message, conn)


def set_aside(skipped: list, item: str, reason: str) -> None:
    """Name a row the import passed over on purpose. Nothing is wrong, so nothing is logged.

    "Quick import" sends the whole scan back, already-tracked rows included -- the confirm
    dialog counts them out loud before it sends. They are the nominal case of that button, so
    they are not failures: routing them through `errors` painted a successful re-import red,
    and logging each one wrote a journal line per tracked service on every click (measured:
    twenty tracked hosts, twenty lines, every time). `check_all` in `app/api/services.py` had
    already settled that question for the same reason -- "One line for the run, not one per
    service ... a fleet of fifty would otherwise bury everything else in Recent activity" --
    so the run gets its single line at the end and these rows get none of their own.
    """
    skipped.append(f"Import passed over {item}: {reason}")
