"""English wording that depends on a count.

The panel asks the browser for its plurals: `t()` reads the CLDR rules of whichever language
is active, because eight languages do not agree on where zero belongs or on whether a plural
is built by adding letters at all. These sentences never reach it. They are written on the
server and they are English: a validation detail in the provider wizard, a line in the
activity log, the body of a webhook. One language, one rule, and it can live here.

What this replaces is `f"{n} service(s)"`. That is not a plural, it is a note to a reader who
is expected to pick a form themselves, and in a log line nobody proofreads it is simply what
the operator sees: "1 service(s)".
"""


def plural(n: int, singular: str, many: str | None = None) -> str:
    """`1 service`, `3 services`, `0 services` -- English puts zero in the plural.

    `many` is for the nouns that do not take a plain -s; nothing here needs it yet, and the
    parameter exists so that the first one does not have to reopen the call site.
    """
    return f"{n} {singular if n == 1 else (many or singular + 's')}"


def verb(n: int, singular: str, plural_form: str) -> str:
    """The verb that agrees with `plural(n, ...)`: `uses` / `use`, `has` / `have`.

    Separate from `plural()` because the noun and the verb are rarely adjacent in a sentence
    ("{count} service(s) still use ..."), and a single helper returning both would have to
    own the word order, which differs per sentence.
    """
    return singular if n == 1 else plural_form
