# Changelog

All notable changes to this project will be documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) — versioning follows [SemVer](https://semver.org/).

---

## [Unreleased]

### Security

- **A read-only API key could rewrite the state of any route, and a link preview could do it
  with nobody clicking anything.** `GET /api/services/{sid}/check` sat behind
  `require_auth(request)` with no scope, and unscoped means "any authenticated caller": a key
  minted with the default `["read"]` — for a dashboard, for a status page — could rewrite
  `services.status` and `services.last_checked` on any id and append a line to the log. Being a
  GET made it more than a scope mistake. A browser prefetch, a crawler, a link preview or an
  uptime probe that merely follows the URL performs the write; there is no form and no click to
  point at afterwards. The canonical route is now `POST /api/services/{sid}/check` with
  `scope="write"`, and the GET stays one version as a deprecated alias carrying the same scope,
  so the hole is shut on both verbs instead of staying open for the length of a deprecation
  window.

  The guard meant to catch exactly this could not see it: `test_no_write_route_is_left_without_a_scope`
  scans for `@router.post|put|delete|patch`, and this was a GET. A second guard now walks the
  AST of `app/api/*.py`, follows one level into module-level helpers — a route's body is as
  likely to live in a `_helper()` as inline — and fails on any GET route that reaches an
  `INSERT`, `UPDATE` or `DELETE` without a scope.

  **Upgrading:** a script calling the `GET` keeps working this version; move it to `POST`. The
  MCP bridge and the panel already call the POST.

- **The port was published on every interface of the host, and what answers it holds the
  credentials of every provider.** `docker-compose.yml` mapped `"8888:8888"`, which means
  `0.0.0.0:8888`: every machine on the LAN, and whatever a router in front of it forwards.
  That is what a first `up -d` gets — before the setup wizard has run, and therefore
  before there is a password on the panel at all. The mapping is now
  `"${VAUXTRA_BIND:-127.0.0.1}:8888:8888"`, and `README.md`, `.env.example` and
  `docs/DEPLOYMENT.md` say the same thing in the same place they used to say the opposite.
  Widening it is one variable, which is the point: reaching the LAN is now a decision
  somebody makes rather than one they inherit.

  **Upgrading:** an instance you reach from another machine on `http://<host>:8888` stops
  answering there. Put `VAUXTRA_BIND=0.0.0.0` in `.env` to get it back, or — better —
  put the reverse proxy of `docs/DEPLOYMENT.md` section 5 in front of it and leave the port
  where it is.

- **Nothing stopped a tool from signing a commit on behalf of somebody who never wrote
  one.** GitHub builds the contributor sidebar from commit authors and from
  `Co-authored-by:` trailers alike, so a trailer is not a footnote: it puts a name and an
  avatar on the front page of a public repository. Two arrived that way, and removing them
  cost a rewrite of every commit and a force-push of six tags. That rewrite cleaned the
  history and left the door it came through wide open.

  `scripts/check_repo_hygiene.py` now reads the author, the committer and every
  `Co-authored-by:` trailer of every commit in the repository, refuses any address outside
  a declared allowlist, and refuses four substrings that name a process rather than a
  person. The merge identity `GitHub <noreply@github.com>` may commit and may not author,
  because an address that can only ever be a committer must not be able to become a
  contributor. Ten tests build throwaway repositories containing exactly the shapes it has
  to refuse, because a gate nobody has watched fail is a claim rather than a guarantee.

  It also refuses to rule on a shallow clone instead of passing one in silence, which is
  why both workflows that run it now check out with `fetch-depth: 0`. `actions/checkout`
  fetches one commit by default: on that default the gate would have read the tip, said
  nothing about the sixty commits behind it, and reported green for a history it never
  opened.

- **The runtime image shipped fourteen fixable CRITICAL/HIGH CVEs, and the removal of pip had
  quietly stopped happening.** Two independent defects in the same `Dockerfile`, one of them
  red on every build, the other invisible.

  `python:3.14-slim` is rebuilt on its own schedule, and between two of its rebuilds Debian
  publishes security updates for packages already baked into it. With no `apt-get upgrade`,
  the image never receives them. perl-base carried three CRITICAL advisories; libsqlite3-0,
  libpcre2-8-0 and gzip carried HIGH ones; every one of those fixes was already sitting in
  the Debian archive. The image scan gate failed on exactly this, and it failed on something
  upstream rather than on anything written here, which is why nothing in the source looked
  wrong.

  The second defect never failed anything. The two `rm -rf` calls that remove pip and the
  bundled `ensurepip` wheel named `/usr/local/lib/python3.13/...`, and the base image had
  moved to 3.14. Removing a path that does not exist succeeds and prints nothing, so from
  that version onward the removal simply stopped happening while the build stayed green. The
  1.8 MB `ensurepip` wheel, and with it a complete copy of the vendored dependency tree that
  the comment above spends fifteen lines arguing must go, shipped in every image since. Both
  directories are now asked of `sysconfig` instead of being spelled out, and the build fails
  outright if `ensurepip` survives its own removal, so the next Python bump cannot break this
  the same silent way.

  Measured on the rebuilt image: zero fixable CRITICAL or HIGH findings, OS packages and
  Python packages alike, against fourteen before.

- **An invisible character pasted into a permission column granted admin.** `_split_scopes`
  called a bare `str.strip()` on each segment of the stored `scopes` column, and the boundary
  that draws is the Unicode blank table, not a rule anyone had written down. Measured against
  a live admin route, `GET /api/settings/api-keys`, with a key whose column held one code
  point in front of `admin`: U+0009, U+000A, U+0020, U+00A0 (no-break space) and U+2007
  (figure space) all answered **200, admin granted**; U+200B (zero-width space) answered
  **401**. Three invisible prefixes opened the admin routes and a fourth did not, and no line
  in the source said which was which. The padding is now named — `_SCOPE_PADDING =
  string.whitespace` — and it is the whole of it: the six ASCII blanks, the ones a keyboard or
  a shell produces. After: U+0009, U+000A and U+0020 still grant admin, and **U+00A0, U+2007
  and U+200B are all refused with 401**. A pasted blank stays part of the token, so the scope
  becomes a word the build has never heard of, `_get_auth_context` turns the key away, and the
  warning it logs prints the stored value, which is how an operator discovers that the column
  holds something invisible. `" admin"` with an ordinary space still grants admin: that one
  was already a decision, pinned in `tests/test_api_key_scope_residues.py`, and it stays one.

  **Upgrading:** a key whose `scopes` column was pasted out of rendered text stops
  authenticating and says so in the log. Revoke it and create a replacement.

### Added

- **A frontend test runner, because three fixes in a row shipped with the same caveat.** The
  bodies of the last three pull requests each ended by saying their defect could only be
  reproduced by hand — cut the backend, open the wizard, watch a green tick appear over a
  failure — and each of them was right: nothing in this repository had ever rendered a
  component. `npm run test` does, on vitest and jsdom, and it runs inside `npm run quality`
  and as its own step in the Node job of CI.

  The twenty tests that come with it are not a sample of the codebase; they are the defects
  those three pull requests fixed, written down. Every one was checked the only way a
  regression test can be: by putting the defect back and watching the test go red. Two rules
  keep them honest. They render without `I18nProvider`, so `t()` returns the key and an
  assertion reads `setup.import.scan_failed` rather than an English sentence somebody may
  reword next month. And `@/api/client` is redirected to a stub at resolution time rather
  than mocked file by file, so a test that quietly reaches for the network reaches nothing
  at all — a test whose result depends on what happens to be running on the machine is not
  a test.

- **The API and the MCP bridge are compared on what they accept, not just on what they
  answer.** `scripts/check_api_mcp_parity.py` matched the bridge's 84 tools to the API by
  method and path, which catches a tool pointed at a route that does not exist and nothing
  else. Every constraint on the way in was outside the comparison. The checker now walks the
  AST of both sides: it resolves Pydantic model inheritance, reads `Literal` members,
  `Field(ge=…, le=…)` bounds and the membership and range predicates written inside
  `field_validator` bodies, and compares each against the matching tool's signature.

  Run against the tree this work started from, it reported **25 divergences** in four shapes.
  Sixteen were a closed set declared as an open `str`: `forward_scheme` against
  `Literal["http", "https"]`, `expose_mode`, `public_target_mode`, a bulk `action` against
  three verbs, a provider `type` against ten names, a tag `color` against fourteen. Five were
  `target_port` as an unbounded `int` against a 1–65535 bound. Two were a parameter the tool
  demanded that the route does not — `create_api_key`'s `scopes`, `validate_provider_draft`'s
  `url` — so the assistant was being refused a body the API would have taken. The last two
  are `apply_template` inventing a domain and a port for a template that names neither, which
  is written up under **Fixed** below — the checker found that one rather than agreeing with
  it afterwards.

  `CONTRACT_DIVERGENCE_COUNT` is now **0**, and the build fails when it moves. Twenty tool
  calls are compared against a model, three findings are exempt with the reason recorded
  beside each, and ten routes validate a plain dict by hand and so carry no contract for a
  tool to declare. That last number is printed with the routes named, rather than passed over
  silently, so the size of the un-compared remainder is visible instead of implied.

- `tests/test_unique_violation_race.py` runs the race itself rather than describing it: a
  second connection commits the competing key at the instant the route issues its INSERT, so
  the lookup reads a table without the row and the INSERT meets one with it. It also pins the
  two halves that must both survive — the lookup still refuses the ordinary duplicate without
  reaching the index, and a locked base is still a 500.

- `tests/test_docker_fault_boundary.py` drives a fault through each of the three helpers and
  reads the status code, keeps the daemon's own refusals at 502 (including the lazy image
  lookup), and reads the boundary back out of the source so a helper moved inside the guard is
  red the day it moves. The reader carries a positive control built from the shape the faults
  were measured on, so a rule that matched nothing could not pass for a clean result.

- `tests/test_docker_import_outcomes.py`, 17 tests over the four things that can happen to a
  ticked container, and `tests/test_api_key_write_scope.py`, which walks the router instead of
  keeping a list of paths (see **Changed**). The Docker file holds the two import routes to
  one contract by identity — it asserts that both modules hold the *same* function objects,
  not that their source looks alike, because two routes that merely resemble each other are
  exactly what drifted apart in the first place.

- `expose.preflight.detail.dns_target_required` and
  `expose.preflight.detail.dns_target_detection_failed`, in all eight locales. The preflight
  has been emitting both detail keys from `describe_public_target_failure()` since the DNS
  target check was split in two, and the panel had a translation for neither, so the one
  reachable state a first-time operator meets — no public target set, on a hostname that needs
  one — fell through to the raw English sentence in every language. They are the two halves of
  the same gate: an address was never entered, or automatic detection ran and found nothing
  usable. Each says which of the two it is and what to do about it, which is the whole reason
  they were split.

- Six counted sentences for the restore outcomes below:
  `settings.backup.restore_settings_dropped`, `settings.backup.restore_domains_skipped`,
  `setup.restore.done_settings_title`, `setup.restore.done_settings`,
  `setup.restore.done_domains_title` and `setup.restore.done_domains`. The bodies are plural
  families and not just the titles, so that French, Spanish, Portuguese, German and Dutch
  agree at one as well as at many; `{count}` selects the form even where the text does not
  repeat the number.

- **A runtime parity gate**, `scripts/check_runtime_parity.py`, run in the backend job beside
  the API-MCP parity and repo hygiene gates. It takes the two `FROM` lines as the single source
  of truth and holds every other declaration of the runtime to them: the `setup-python` and
  `setup-node` pins across the workflows, and every sentence that tells a reader what the
  Dockerfile contains, the comments inside the workflows included. That last reading is not
  thoroughness for its own sake. The stale sentence in `security.yml` had wrapped across two
  comment lines mid-token, so a grep for `python:3.13-slim` could not see it and neither could
  the first version of this gate; a claim about the runtime now has to be written on one line
  to survive review, which is the price of being checkable. A pin written as an expression is
  reported rather than passed over, because passing quietly is the silence the gate exists to
  end. It deliberately does not read `ruff.toml`'s `target-version` or the README's "Requires
  Python 3.13+": those state the source floor, which is a separate policy from what the image
  runs and is allowed to sit below it. `tests/test_runtime_parity_gate.py` builds a throwaway
  repository for each of the gate's readings and moves exactly one declaration in each, so the
  checks can be told apart; it rebuilds the shape 834dfc3 left behind and confirms the gate
  calls all seven of its disagreements; and one test runs the gate against this repository, so
  `python -m pytest tests/` catches the next such bump even with the CI step removed.

### Fixed

- **`CONTRIBUTING.md` asked for a Node version the toolchain does not accept.** It said
  "Requires **Node.js 22+**", and nothing in the repository backed that number. The Python
  floor is declared twice — the README sentence and `ruff.toml`'s `target-version = "py313"` —
  while `frontend/package.json` carried no `engines` field at all, so the Node floor was prose
  and only prose.

  The real requirement is `^22.22.2 || ^24.15.0 || >=26.0.0`: the intersection of all 62
  distinct `engines.node` ranges in `frontend/package-lock.json`, and today `jsdom`'s own
  range, the tightest in the tree. Node 22.0 through 22.22.1, all of 23.x, 24.0 through 24.14
  and all of 25.x satisfy "22+" while packages in the tree — `jsdom` and `vitest` among them —
  declare them unsupported, and `npm` reports `EBADENGINE` for each.

  `frontend/package.json` now declares the range, so npm checks a contributor's interpreter at
  install time instead of leaving a sentence to do it. It is deliberately not added to
  `scripts/check_runtime_parity.py`: that gate reads what the published image runs, and says in
  its own docstring that a source floor is a separate policy, allowed to sit below the image
  and not read there. Nothing failed and nothing would have, because CI has only ever built on
  Node 26 — the cost was paid by whoever installed what the document asked for.

- **Eight API routes appeared in no document at all.** The reference in `docs/HOWTO.md`
  listed 82 of the 90 routes the application serves. Missing were `GET /api/services/{sid}` —
  the plainest read in the API, and the one an operator writing a script reaches for first —
  `POST /api/services/bulk`, and all six direct per-provider record routes
  (`/api/providers/{pid}/dns-records` and `/api/providers/{pid}/proxy-hosts`, GET, POST and
  DELETE each). That last group is a whole capability: not an omitted row but an omitted
  section, and a search of every tracked document for `dns-records` or `proxy-hosts` returned
  nothing. They are all authenticated, none is deprecated, and nothing failed while they were
  invisible.

  All eight now have rows, and the six provider routes have the paragraph they need more than
  the rows: they reach past the service table into the provider itself, so a record written
  through them is one Vauxtra does not know it owns and the next drift check reports it.

  `scripts/check_api_mcp_parity.py` now asks of `docs/HOWTO.md` the question it already asked
  of the bridge's README — every route documented, and every documented route real. The
  bridge's own reference was gated and the operator's was not, which is the whole reason these
  eight had time to disappear. One exemption is allowed, `GET /api/services/{sid}/check`,
  documented inside the row for the `POST` because the sentence exists to say the GET is
  deprecated; like the route allowlist above it, an exemption that stops matching fails the
  build.

- **Four documentation links pointed nowhere.** `README.md` sent readers to
  `docs/HOWTO.md#10-mcp-integration`, but that section is now `## 11) MCP Integration`, so the
  anchor had been silently scrolling nowhere since the section above it was added. The three
  links in `docs/DEPLOYMENT.md` were written root-relative (`docs/HOWTO.md`, `SECURITY.md`)
  inside a file that already lives in `docs/`, so GitHub resolved them to `docs/docs/HOWTO.md`
  and 404ed. A dead relative link and a dead anchor both render as a perfectly ordinary link,
  which is why neither gets reported.

- **The MCP bridge announced itself as version 3.2.4, which is fastmcp's.** `FastMCP(...)`
  takes a `version` keyword and `vauxtra_mcp/app.py` passed none, so the handshake answered
  `serverInfo: {"name": "Vauxtra", "version": "3.2.4"}` — a release of Vauxtra that has never
  existed, and one that would move on its own with the next library bump. Found against a real
  stdio client speaking protocol `2025-06-18` rather than in process: every test of this bridge
  calls its functions directly, which proves the tools are right and says nothing about what a
  desktop client is told while connecting. The bridge now declares `__version__` in
  `vauxtra_mcp/__init__.py` and passes it. It deliberately does not read `app.config` — this
  package imports nothing from `app`, and reaches an instance over HTTP that may be running a
  different release — nor `APP_VERSION`, which the Dockerfile stamps at build time into an
  image the bridge is deliberately not in, so out here it would always read `dev`.

  `tests/test_version_declarations.py` holds the declaration to the version in
  `frontend/package.json`, and holds the built instance to the declaration. Neither half is
  hypothetical: `v1.0.2` was tagged while `package.json` still said `1.0.1` and nothing
  anywhere noticed, and dropping the `version=` argument again leaves a bridge that still
  starts, still lists all 84 tools, and quietly goes back to answering `3.2.4`.

- **`pip-audit` never looked at the MCP bridge's dependencies.** The step named
  `requirements.txt` and nothing else, and neither `fastmcp` nor `httpx` appears in that file:
  both are declared in `vauxtra_mcp/requirements.txt`, which nothing in this pipeline read. No
  other scanner covered them. The image scan cannot, the bridge not being in the image, and
  Syft drops every requirement line that is not an exact pin, so both were missing from the
  SBOM that Grype — the only step here allowed to break the build — compares against. That
  left the bridge's whole dependency tree, `authlib`, `joserfc`, `pyjwt` and `mcp` among them,
  audited by nothing, in the one component an operator points at their own panel holding an
  API key. The step now passes both files. It reports no advisories today: the defect was the
  blind spot, not a number.

- **Two test modules ran against the database of whatever checkout they were on, and one
  of them emptied a table on it before every test.** `test_templates_api.py` and
  `test_metrics_endpoint.py` set `DATA_DIR` and `DB_PATH` in `os.environ` and called
  `init_db()`, under a comment reading "Redirect DB to a temp file so tests are isolated".
  `app/config.py` builds both paths with `os.path.join` from its own location and never
  reads the environment, so neither assignment moved anything. The templates module then
  ran `DELETE FROM service_templates` in `setUp`, once per test, on the real file — and
  every assertion still held, because a table the test had just emptied and refilled is
  exactly what the test expected to find. Measured on a working checkout: a marker row
  inserted beforehand was gone afterwards, one fixture row was left behind, and the run
  reported 17 passed. Both modules now rebind `DATA_DIR` and `DB_PATH` on `app.models` and
  `app.db`, the way `IsolatedDBTestCase` already did, and restore them in teardown.

  Nothing could have caught this, so `tests/conftest.py` now can: it hashes
  `data/vauxtra.db` before the session and after it, and fails the run if the file changed,
  appeared or vanished. It does not inspect how a test redirects the database, only whether
  the operator's own file came out of the run as it went in — which is the property that
  was actually violated, and the one that stays true however a future test gets it wrong.
  The three `os.environ` keys the two modules set are gone with them: `DISABLE_AUTH` was
  read by nothing in `app/` or `vauxtra_mcp/`, and the tests had been passing on the
  implicit admin scope that `_get_auth_context()` grants when no password is configured.

  It found a third case on its first full run, in a file whose own docstring says every
  HTTP call is mocked — which was true, and the call it missed was not an HTTP one.
  `_resolve_tunnel_id()` warns through `add_log()` when Cloudflare returns several tunnels
  and it cannot choose, so `test_resolve_returns_empty_for_multiple_tunnels` appended a
  line to the operator's activity journal on every run of the suite. `add_log` is patched
  there now, and the warning is asserted rather than discarded: it is the only thing that
  branch produces for the operator, it names the tunnels so the choice can be made, and
  nothing had been looking at it.

- **deSEC and PowerDNS reported their zone check in English, inside a panel that was
  otherwise translated.** Both answer their "Domain match" / "Zone match" check with the
  short code `zone_match` or `zone_missing`, and the diagnostics panel looks up
  `providers.diag.detail.<code>` to say it in the operator's language. Neither key had ever
  been written, in any of the eight locale files. Nothing broke, and that is exactly why it
  lasted: `checkDetailText()` compares what the lookup returned against the key it asked
  for, and falls back to the English sentence the API sends beside the code. A French,
  German or Japanese operator saw one English line among translated ones, with nothing to
  mark it as a defect rather than a sentence somebody chose not to translate. Both keys now
  exist in all eight locales.

  The two ends never referenced each other — one is a string literal in a provider, the
  other a line of JSON — so nothing was in a position to notice. `check_detail_code_parity.py`
  now joins them in CI: it reads the codes out of the backend and asks `en.json` about each
  one, in one direction only. A code with no key fails, because that is decidable: the code
  was found, and the key either exists or does not. A key no code claims does not fail and
  is not reported, because extraction cannot be complete — a code can arrive through a
  variable, or inside a tuple — so an unclaimed key is evidence about the script, not about
  the repository. An early draft flagged three that way, and all three were emitted by
  shapes it could not yet read.

- **Three rows that could never become a service were reported as "all services already
  imported".** `POST /api/services/import` answered with two fields, `imported` and
  `errors`, and `errors` only ever carried the message of an exception. Every other outcome
  was a bare `continue`, which meant the panel had one branch for all of them: when nothing
  was imported and nothing raised, it showed the green *Sync complete - all services already
  imported*. For twenty routes Vauxtra already tracks, that sentence is true. For a proxy
  host the provider listed with no domain name, a DNS record answering with no address, or a
  single-label name with no dot to split on, the identical path produces the identical
  sentence, and it is false in every word: those rows were not imported, are not tracked,
  and never will be until somebody fixes them at the provider. Nothing in the answer, and
  nothing in the journal, told the two cases apart.

  The route now answers with four outcomes: `imported`, `linked`, `skipped` and `errors`.
  `imported` and `errors` keep the meaning and the shape they had, so every existing caller
  keeps working. `skipped` names the rows passed over on purpose, which is not a failure and
  must not be painted as one. `linked` counts a write that was previously counted nowhere:
  an existing service gaining the DNS half it was missing is a real `UPDATE` that creates no
  row, so it fell through every field of the old answer and the operator was told zero.

  Refusals are named rather than counted — *"Import skipped proxy host 91 on the provider:
  the provider listed no domain name for it"* — and a row that raises names itself and its
  provider, where the old answer surfaced a bare `invalid literal for int() with base 10`
  with nothing to attach it to.

  Two providers answering for the same name used to resolve silently and by the last one
  seen, which was not something the code could promise: providers are read with no
  `ORDER BY`, so the winner depended on the order SQLite happened to return. The first
  record is now the one kept, both providers are named, and the message says which answer
  was imported, because the loser is the row the operator has to go and remove. One provider
  holding that name twice reads differently — a duplicate inside one integration, not two
  integrations disagreeing — and no longer produces a sentence naming the same provider
  twice.

  Apart from one deliberate fold, every submitted row lands in exactly one of the four: a
  DNS record whose name matches a proxy host in the same payload is the other half of that
  host, not a second service, so the pair is counted once under `imported`. The route
  docstring, `tests/test_import_outcomes.py` and the MCP tool that wraps the route all say
  so in those terms, because a reader who adds the four up is owed the exception rather than
  left to discover it.

  The journal follows the rule `check_all` already set: one line for the run, not one per
  row. Twenty rows already tracked add a single line saying twenty, where a line each would
  bury the rest of *Recent activity*. Refusals still get one line apiece, because those are
  the ones with somewhere to go and something to fix.

  Naming four outcomes on the wire changes nothing until something reads them, and neither
  screen did. The settings panel read `imported`, and read `errors` only when `imported` was
  zero: a batch that created two services and refused a third reported the two and swallowed
  the refusal, and a batch that only linked reported nothing at all. The setup wizard was
  worse in one specific way — it announced refusals under the word *skipped*, which is the
  name of the other outcome, so the one case the operator has to go and fix was labelled
  with the one they can ignore. Both now raise a line per outcome, and the counted line is
  enough on its own because the sentence behind it is in *Recent activity*, which the same
  handler refetches.

  *Sync complete — all services already imported* is gone with them. It was the fallback for
  every quiet run, and now that rows set aside are counted and named on their own it was
  claiming something the answer no longer needs it to claim; what is left for that line is
  the run that had nothing to act on, so it says *Nothing to import* in all eight languages.

- **A setting could be stored and never take effect, under a green "Saved".**
  `POST /api/settings` writes the row, commits, and only then hands the value to the running
  scheduler. That order is the right one — the database is the record and the scheduler is a
  consequence of it — but the window it opens was handled by
  `except (ImportError, TypeError, ValueError): pass`, and each of those three was wrong in
  its own way.

  Two of them could not happen. `_validate_setting` stores `str(number)` for every key in
  `_SETTING_RANGES` or refuses the whole payload with a `400`, so the `int()` that follows
  cannot raise `TypeError` or `ValueError` on a value that reached it: two thirds of the
  clause were guarding against a state the validation above already makes impossible. The
  third could and did. A scheduler module that fails to import leaves `check_interval`
  written and the health checks running at the old value, answered with
  `{"ok": true, "saved": ["check_interval"]}` — and confirmed by a settings page that reads
  the stored value back and shows the operator exactly the number they typed. Nothing
  anywhere said the two had diverged.

  Everything the clause did *not* name went the other way and left the route as a `500`, for
  a setting that had already been written. The obvious reaction to that is to try again,
  which repeats a write that succeeded.

  The answer now carries `not_applied` beside `saved`, because those answer two different
  questions and both can be true at once, and the journal carries the exception that caused
  it. The stored value stands: it was committed before the scheduler was called, it is the
  record, and rolling it back to agree with a scheduler that is itself broken would throw
  away the operator's input to make the two agree on the wrong one. `auto_reconcile` keeps
  its `int()` inside the guard, unlike `check_interval`, because it reads its value back out
  of the database rather than from the payload just validated, and a row written before
  `_SETTING_RANGES` existed can still hold a word.

- **A restore dropped rows out of the file and answered `{"ok": true}`.** Two `continue`
  statements in `POST /api/restore` decided the fate of rows and said nothing about it. A
  domain row carrying no name could not be written, and every service that referenced it
  came back pointing at a domain the list no longer offers — which the operator meets on
  the next edit, with no reason to connect it to the restore. A setting the running version
  does not accept went the same way, and *dropped* is the accurate word rather than *kept*:
  the restore empties the `settings` table before refilling it, so an unaccepted key does
  not retain the value this instance already had, it ends up absent.

  The answer now carries `settings_not_restored`, naming each one, and
  `domains_without_name`, counting them. The journal gets one warning line per category,
  never one per row, and the sentence agrees with the count in the noun, the verb and the
  auxiliary rather than in the noun alone.

  The hard part was not detecting the loss, it was not crying wolf. Six keys travel in every
  backup file and are dropped on purpose by every restore: the five in `_PROTECTED_SETTINGS`
  — the admin hash, the setup marker, the schema version, the auth mode, the session epoch,
  each belonging to the instance in front of you rather than to the file — plus
  `webhook_log_purge_done`, a one-shot migration marker whose loss costs one idempotent
  re-run at the next start. Naming those would have fired on every single restore and taught
  the operator to skip the line that matters. `_RESTORE_DROPS_ON_PURPOSE` is therefore built
  from `_PROTECTED_SETTINGS` instead of written out by hand, so a key added to the protected
  tuple cannot start being reported as lost, and `tests/test_restore_reporting.py`
  fails if that calibration is ever given up.

  **Upgrading:** the four fields the panel already reads — `ok`, `services`, `providers`,
  `webhooks_needing_url` — are unchanged; the two above are additions.

- **A template could hold every value a service refuses, and the refusal arrived at the
  worst possible moment.** A service template is a `POST /api/services` payload saved once
  and applied many times, and `TemplateIn` validated three of its fields.
  `public_target_mode: "garbage"`, `dns_ip: "not an address"` and a domain no zone will ever
  carry were each stored with a `201`. Nothing was wrong until the template was used, and
  then the refusal named a field the operator was not editing, on a form they had just
  opened, about a decision somebody else took weeks earlier. `domain`, `dns_ip` and
  `public_target_mode` are now checked exactly as `ServiceIn` checks them, with the one
  difference that is the whole point of a template: any of them may be left empty, because
  filling it in is the operator's job at apply time. A value that is *present* is a value
  the service route will be handed verbatim, so it is refused once, where it was typed.

  The two kinds of id a template names failed in two different ways, neither of them
  useful. `proxy_provider_id`, `dns_provider_id` and `tunnel_provider_id` are real foreign
  keys — `service_templates` declares `ON DELETE SET NULL` on all three — so an id naming no
  row reached SQLite and came back as an `IntegrityError`: a `500` on a form that was
  correct apart from one dropdown. `tag_ids` lives in `tag_ids_json`, a TEXT column no
  constraint reaches, so an unknown tag was stored without a word and handed back by
  `GET /api/templates/{id}/apply` for as long as the template existed; the refusal finally
  arrived from `POST /api/services`, naming a tag id nobody had chosen. Both are now checked
  before the write, by the same `_unknown_references` shape `app/api/services.py` has used
  all along, and both answer `400` naming every unknown id — `Nothing was created -- unknown
  tag 12, provider 4` — with nothing written.

  A reference that rots *after* the save was the other half of the same asymmetry. A deleted
  provider empties its column, because the foreign key says so. A deleted tag left its id in
  the JSON and the panel went on sending it. Every read now drops ids naming tags that no
  longer exist, so a tag rots the way a provider already did: quietly, before anybody is
  asked to do something about it. Dropping it on `apply` alone would have been worse than
  not dropping it at all — the editor draws one chip per existing tag, so a dead id has no
  chip to click off, and the refusal added above would then have blocked the save for
  something the form gave no way to remove. The stored column is left untouched, so a tag
  deleted by mistake and put back by hand returns to the templates that named it.

  `tag_ids` now always reads back as a list of ints. `{"a": 1}` is valid JSON, so the old
  `except` never fired and the object reached the panel under a name every caller reads as a
  list of ids.

- **Two hostname halves that each fit could be saved as a name no DNS zone will ever carry.**
  A 250-character subdomain and `example.com` were each inside their own 253-character limit,
  so `POST /api/services` answered `201` and stored a 261-character FQDN. Nothing downstream
  caught it: the route was live in the panel, and each provider refused the record separately
  at push time, later, phrased as a provider failure rather than as a name that was never
  publishable.

  `app/validators.py` gains `fqdn_problem(subdomain, domain)`, alongside `FQDN_PROBLEMS` and
  `FQDN_REASONS`, measuring the joined name exactly as `_service_fqdn` builds it so the value
  checked is the value published. `ServiceIn` applies it in a second `@model_validator`, since
  a rule about the pair cannot live on either field — and `ServicePreflightIn` inherits it, so
  the wizard's dry run refuses the same name the save would.

  The panel carries the mirror as `fqdnProblem` in `frontend/src/lib/hostname.ts`. It shows in
  the subdomain field's error slot: that is where the `Final route` preview already lives, and
  the subdomain is the half an operator can shorten, the domain coming from a configured list.
  It is evaluated only once both halves are individually sound, so a name that is malformed
  *and* too long reports the first thing to fix rather than the second. `Continue` refuses it
  before the round trip, and the composite preview disappears while it stands.

  `hostname.cases.json` gains an `fqdn` block of eight pairs run through Python and TypeScript
  in two different CI jobs, like the other two blocks. Both halves of every pair are asserted
  valid on their own, because a pair whose subdomain breaks `label_length` would be refused by
  a field rule first and would leave the composite rule untested. The boundary is pinned at
  both ends: 253 characters is accepted, 254 is not.

- **A subdomain the API would refuse was offered to the operator as a finished hostname, and
  the refusal, when it arrived, did not say what was wrong.** Typing `vaux_dev1` — an
  underscore, which RFC 1123 does not allow in a hostname and which several DNS APIs
  nonetheless accept in other record types — produced a hint reading *"Final route:
  vaux_dev1.<domain>"*, a `Continue` that spent a round trip, and a 422 the wizard rendered
  as *"the checks could not run"*. Four layers had to agree on the mistake for that to happen,
  and all four are closed.

  `app/validators.py` no longer answers with a boolean. `subdomain_problem()` and
  `domain_problem()` return a stable code — `empty`, `too_long`, `dot_edge`, `wildcard`,
  `charset`, `label_length`, `hyphen_edge`, plus `url`, `no_dot` and `ip_address` for a domain
  — and `is_valid_subdomain`/`is_valid_domain` are now that code being `None`. `ServiceIn` and
  `POST /api/settings/domains` turn it into an English sentence, so a client with no
  translations reads *"a subdomain holds lowercase letters, digits, hyphens and dots only"*
  where it used to read *"Invalid subdomain"*.

  The panel carries the same rule in `frontend/src/lib/hostname.ts`, a field having no way to
  ask the server on every keystroke. Both hostname fields now turn red with their own sentence
  as they are typed, in all eight languages; `Continue` refuses before the round trip rather
  than after it; and the `Final route` hint disappears while either half is invalid, since it
  was a promise about a name the server had already decided not to accept. A 422 that does
  arrive now says *"the route was refused before the checks ran"* and lands in the form's own
  banner — scrolled into view, `Continue` sitting at the bottom of a body that scrolls and the
  banner at the top of it — instead of only in a toast in the opposite corner.

  Two copies of one rule is the arrangement where one of them quietly stops agreeing, so
  neither file owns it. `frontend/src/lib/hostname.cases.json` holds 53 values and the verdict
  each one earns; `tests/test_hostname_rules.py` runs that table through Python and
  `src/lib/hostname.test.ts` runs it through TypeScript, in a different CI job, so a change to
  one side alone turns the other red. The table must also reach every code the module declares
  and accept at least five values, because a table that only ever refuses would pass a
  function that refuses everything. Where the two sides are allowed to differ — Python asks
  `ipaddress.ip_address`, the panel reads the shape, and they can disagree on whether
  `abc:def` is a bad charset or a bad address — only the verdict is asserted, never the code.

  The rewrite widened what is accepted, deliberately. The old expression demanded a single
  label, which refused `vaux-dev.sous` and `*.vaux-dev`: both ordinary DNS, and all four
  providers derive the zone by walking labels from the right, so none of them ever needed the
  restriction.

- **Every counted sentence in the panel used the English plural rule, in all eight languages.**
  There was no plural machinery: each call site that needed one wrote the test by hand, and
  each one wrote `count === 1 ? singular : plural`. That is the English rule. French and
  Portuguese put zero in the *singular* category, so an empty integrations page read
  "0 intégrations" and an empty template library "0 modelos", both wrong in a place an
  operator meets on their first run — the empty list is the first thing a new install shows.
  Japanese and Chinese have a single form and were being handed a singular `Intl.PluralRules`
  can never select, five dead keys a translator had written for nothing. `t()` now asks the
  CLDR rules of the active locale, through one `Intl.PluralRules` per language, and the five
  hand-written selectors are gone.

  `t()` also writes `{count}` itself now, with the locale's grouping separators. Six call
  sites used to format the number and hand `t()` a string, which is not a number and so could
  not have selected anything: the plural choice and the printed number now have one writer.
  A count that still arrives as a string resolves to the `_other` form rather than to nothing,
  because a sentence in the wrong plural is a wording bug and a raw `providers.meta.count`
  painted across the page is a broken build.

  `check-locale-parity.mjs` no longer demands the English key set of every file, which would
  force that dead Japanese singular back in. It reads `LOCALE_TAGS` out of the app so the
  check and the runtime cannot drift, asks each locale which categories it declares, and
  requires `_other` everywhere and `_one` wherever the language has one; the language's
  remaining categories are allowed and never required — French declares `many`, and it fires
  at a million routes — while a category the language does not declare is refused by name:
  *"ja-JP has no 'one' plural category, this form can never be selected"*. A bare key
  colliding with a plural base is refused too, `t(base)` and `t(base, {count})` having no
  business reading the same name differently.

  Also renamed `monitoring.check_one` to `monitoring.check_row`: it was never a plural. It
  means "check this one host now", and it sat next to real plural keys wearing their suffix.

- **Thirty-three sentences wrote their own plural inside a parenthesis, and no language reads
  it.** "1 certificat(s)", "1 service(s) utilise(nt)", "99 % sur 1 contrôle(s)": the crutch is a
  note to a reader who is supposed to pick a form themselves, and it shipped in all eight
  files. It also hid that four of these languages do not build a plural by adding letters to
  the end. Spanish drops the written accent once the plural adds a syllable (`conexión` →
  `conexiones`), Portuguese replaces the ending outright (`verificação` → `verificações`,
  `túnel` → `túneis`), Dutch doubles a vowel in the singular and not in the plural
  (`certificaat` / `certificaten`), and German fronts the stem vowel (`Eintrag` / `Einträge`).
  A parenthesis cannot express any of those, so the crutch was not merely lazy, it was
  unwritable in half the catalogue. Each of the thirty-three is now a `_one` / `_other` pair.

  The crutch also only ever marked the noun, never the verb. "{count} service(s) still point
  at {name}" has a second word that agrees, and every sentence with one had its singular
  rewritten by hand rather than mechanically: eighteen keys across French, German, Spanish,
  Portuguese and Dutch, plus three machine translations that had put the number in the wrong
  place to begin with ("Los servicios {count} utilizan" for "{count} servicios usan").

  `monitoring.tunnels.connections` was not a plural at all but two of them in one sentence,
  "{connections} connexion(s), {clients} client(s)", and one count cannot choose two forms. It
  is now two counted keys and a joiner that owns the separator, which was the real defect
  underneath: Japanese separates a list with `、` and Chinese with `，`, and the old key had a
  comma hard-coded in all eight files. `monitoring.check_summary` carries three numbers of
  which only the first inflects, so its selector is renamed `{checked}` → `{count}` and the
  Spanish wording moved to an invariable tail ("{ok} en línea, {error} fuera de línea").

  `check-locale-quality.mjs` now refuses a short parenthesised ending next to a `{count}`,
  which is what should have caught these in the first place: the parity check counts keys, and
  the quality check held only a table of known-bad translations. `http(s)` and the `(days)` of
  a field label are not touched, having no count beside them and nothing to inflect.

  Also deleted `monitoring.tunnels_down`, written in all eight files and read by nothing: the
  connectors card has shown "{healthy}/{total} healthy" for some time. It would otherwise have
  become two dead keys instead of one.

- **Fourteen sentences the server writes itself carried the same crutch, and one of them got
  five words wrong in the dialog that asks before deleting a provider.** These never reach
  `t()`: they are composed in Python, in English, and they surface in the activity log, in the
  validation detail the provider wizard shows when the panel has no wording of its own, in a
  cert-expiry alert, in a webhook body, and in the 409 that answers `DELETE /api/providers/{id}`
  when services still point at it. That last one read `1 service(s) still use "AdGuard"`, and
  because the parenthesis only ever marks the noun, the paragraph under it had been written for
  the plural and left there: *"1 of them have no other target: they keep the public hostname and
  stop being published anywhere"*, then *"1 go on being published by their other targets"*. Five
  disagreements in the sentence an operator reads while deciding whether to remove a target, and
  the crutch is why nobody saw them — `service(s)` looks deliberate, so the eye stops checking.

  `app.text` now holds `plural()` and `verb()`. English only, and deliberately so: the panel
  asks the browser, because eight languages disagree about where zero belongs and about whether
  a plural is built by adding letters at all, while these sentences have one language and one
  rule. `plural(0, "service")` gives "0 services" — English puts zero in the plural, which is
  exactly the assumption the locale files are forbidden to make, French and Portuguese putting
  it in the singular.

  The guard is `tests/test_server_wording.py`, and it reads the AST rather than the lines, so a
  sentence split across three source lines is judged as the one sentence it becomes: that is how
  two of the fourteen had hidden, their `day(s)` sitting on a different line from the number
  feeding it. It refuses an English plural ending glued to a word in a string that also counts
  something, which leaves `http(s)`, `INSERT INTO domains (name)` and `REFERENCES services(id)`
  alone — SQL lives in the same string literals as prose — and it skips docstrings, `app/text.py`
  being a file that has to quote the crutch in order to explain it.

- **The plural machinery only ever inflected the number called `{count}`, and the sentences
  most likely to be wrong put their noun beside a different one.** "{shown} of {total} routes
  shown" reads correctly at every value except the ones an operator sees most: one route, or a
  filter that matched everything. `t()` selects a form from `{count}` alone — every other
  placeholder is printed as it arrives — so twelve sentences carried a noun frozen at whichever
  form the translator happened to write, and nothing in the repository asked about them. The
  quality check had been looking for `{count}` since it was written, which is exactly the
  number that was already safe.

  Ten of the twelve are fixed by moving the noun next to the number it belongs to
  (`services.meta`, `settings.webhooks.enabled_count`, `providers.refresh.failed_count`,
  `templates.count_filtered`, `monitoring.tunnels.healthy_of`), or by counting each noun in its
  own key and interpolating the finished phrase, the way `monitoring.tunnels.connections`
  already did — `dashboard.stats.providers_hint`, `expose.preflight.summary`,
  `services.bulk.result.checked_mixed`, `services.drift.out_of_sync_body`, and the restore
  dialog, where one `{count}` had been asked to serve six different words at once. Which of two
  numbers a noun belongs to is not the same in every language: English, German and Dutch hang
  it on the total where French hangs it on the shown, and moving it is what makes the eight
  files agree on which.

  The other two hard-coded a plural around a joined list and are reachable with one item:
  *"The server refused these settings: check_interval"* and *"These integrations were queried
  and returned nothing"*. Both now have `_one` and `_other` forms whose text never prints
  `{count}` at all — the length is passed only to choose the sentence.

  `layout.nav.item_with_badge` gave up the name `{count}` it was never using as one. It is a
  badge that can read "9+", it has no plural forms and nothing in it agrees with anything,
  and holding the reserved name meant `TranslateFn` could not require `count` to be a number.
  It now can: a call site that formats the number itself and passes a string used to get
  `_other` at every value, silently, and that is now a type error rather than a paragraph of
  documentation asking callers not to.

- **`npx tsc --noEmit` type-checked zero files, and had reported success on every commit for
  as long as the step has existed.** The root `tsconfig.json` carries `"files": []` and two
  project references, and plain `tsc` does not build references: the program resolved no
  sources at all, so the *TypeScript check* job passed unconditionally, whatever the code said.
  It now runs `tsc -b --force`, which builds both referenced projects and ignores the
  incremental cache. Nothing was actually broken behind it — `npm run build` runs `tsc -b` and
  was doing the real work three steps later — but a green check that reads no files is worth
  less than no check, because it is the one people trust.

- **Two MCP contract tests passed or failed depending on which `fastmcp` the resolver
  happened to pick.** `vauxtra_mcp/requirements.txt` asks for `fastmcp>=2.0` and pins nothing,
  and the versions that satisfy it do not agree on what `Tool.run()` raises when its own schema
  turns an argument away. On 3.2.4 pydantic's `ValidationError` comes out directly; on 4.0.3,
  which is what CI resolves today, it arrives wrapped in `fastmcp.exceptions.ValidationError`.
  These two tests named the wrapper, so CI stayed green while anyone who had resolved a 3.x —
  an existing checkout, a lockfile, a machine that installed last month — saw two failures
  reporting a refusal that works perfectly in both. The bridge refuses `action="destroy"` and
  refuses port 70000 either way, names the offending value either way, and sends nothing either
  way; only the class of the exception moved. The alias now accepts both. The neighbouring
  assertions in the same file say `ValueError`, which pydantic's error is, and that is why only
  these two were version-dependent.

  The floor stays unpinned: every requirement in this repository is a floor, and a cap here
  would trade a one-line test fix for a frozen dependency.

- **The locale quality check now asks about every number in a sentence, not just the one it can
  inflect.** A placeholder is either a quantity a sentence could agree with, or a value no word
  inflects around, and every use of the first kind has to say why nothing agrees with it —
  because it is a unit symbol, a position in a wizard, a constant that is never 1, a phrase
  already counted by another key, or a list that is already a string. A name in neither set is
  a hard failure: writing `{routes}` into a sentence now stops the build until somebody says
  which it is, which is the part that keeps this from going stale the next time a sentence
  gains a number. Declarations are per key and not per name, because the same name is both —
  `{providers}` is a list of integration names on the certificates page and a counted phrase in
  the restore dialog, and `{error}` is a message everywhere except `monitoring.check_summary`,
  where it is how many services came back down. A declaration whose sentence no longer prints
  that placeholder fails too, so the table cannot quietly cover a number that comes back later.

  It also compares the placeholder set of every locale against `en.json`, form by form. A
  translation that drops one leaves a sentence missing its number and a translation that
  invents one leaves the braces on screen, and neither was visible to anything here: the parity
  check compares key *names*. Comparing forms rather than keys is deliberate — pooling `_one`
  and `_other` together lets a number dropped from one of them be covered by the other, which
  is the shape of the bug. The single exception is `{count}` in a `_one` form, where "One
  tunnel is healthy" is better than "1 tunnel is healthy" and means the same thing.

- **Two of the three checkers that hold "every locale carries the English key set" had been
  wrong since the files learned to inflect.** `check-locale-parity.mjs` was taught the plural
  categories; `tests/test_frontend_i18n.py` and `tests/test_regressions_v2.py` were not, and a
  Japanese file that correctly omits a singular it can never select failed both. They now
  compare *logical* keys, `certificates.meta_one` and `certificates.meta_other` being one
  sentence, require an `_other` form everywhere, and refuse a bare key that shadows a counted
  base. The per-language rule stays in the `.mjs` alone, it being the only one of the three that
  can ask `Intl.PluralRules` which categories a language actually has; restating it in Python
  would mean two writers of a rule that neither can verify.

- **The 24 h cell printed the same sentence twice, and printed it as a fact when the request
  behind it had failed.** `UptimeStrip` already writes "no check in the last 24 hours" inside
  its dashed box when there is nothing to draw, and the table wrote the same key again in a
  `<p>` underneath, so the cell read the sentence, a gap, then the sentence. The second copy
  hid the real defect. When `GET /api/services/history` had *failed*, the `<p>` correctly said
  so, but the box above it still stated "no check in the last 24 hours" — a claim about the
  infrastructure made from a request that never came back, sitting one line above the sentence
  saying the opposite. An operator reading the box alone would have concluded their scheduler
  was down when all that was down was one fetch. The strip now takes the sentence it should
  print for an empty state, the table hands it the failure wording when the fetch failed, the
  drawer says the same thing as the table, and the `<p>` only appears when there is a
  percentage to state in it.

- **The check button of every row was cut in half, at every window width.** The monitoring
  grid gave the routes card eight of its twelve columns, which sounds like a ratio and is not
  one: the page container caps the grid at 1280px, so the card was 798px on a 1440px screen
  and 798px on a 2560px one. The table's min-content width is 820px. The missing 22px became a
  horizontal scrollbar that parked itself over the last column, and the last column is
  ACTIONS — the per-row "check this service" button. Widening the window did nothing, because
  the cap meant there was nothing to widen. Nor would lowering the table's `min-w`: 820px
  *is* the content minimum, not a floor somebody chose. The split is now 9/3, which gives the
  scroller 906px against those same 820px and leaves the tunnels card 308px for a
  min-content of 237px.

- **The 24 h column read "no check in the last 24 hours" directly above a status cell reading
  "OK, checked just now".** Only the scheduler ever inserted into `uptime_events`. The two
  manual endpoints wrote `services.status` and `services.last_checked` and nothing else, so
  pressing "Check" filled the status cell and left the history the column beside it is built
  from empty. On an instance with automatic checks switched off, the strip and the availability
  tile stayed blank no matter how many checks an operator ran by hand — the page contradicted
  itself, and the contradiction was not a display lag but a row nobody had written. Both
  endpoints now write the same row the scheduler writes.

  `POST /api/services/check-all` got back two things it was already doing the work for. It
  opens a TCP connection to every service, so it measures every latency on the way through, and
  it threw them all away — which is what the footnote under the table was apologising for when
  it told operators to check rows one at a time to fill the LATENCY column. It now returns
  `results`, one entry per probed service with its `status` and `latency_ms` (`null` when the
  target never answered, never a zero pretending to be a measurement), and the page folds them
  into the same store a per-row check writes to. The footnote now names the button that fills
  the whole column in one click, and only appears while the column is still empty; once a check
  has filled it, the sentence was telling operators to do the thing they had just done. And the
  fleet check left no trace whatsoever in "Recent activity" while the per-service check logged
  every probe: it now logs once for the run, one line rather than one per service, so a fleet of
  fifty does not bury everything else in the journal.

- **"Auto checks: waiting for the first cycle" was decided by a column the manual checks write
  too.** The monitoring header chose between two sentences on `last_checked`: a timestamp meant
  the scheduler had run, an empty one meant it had not yet. But `POST /api/services/check-all`
  stamps that same column, so one click on "Check all" turned the header into "Auto checks every
  5 min" on an instance whose `check_interval` was `0` — the value that disables the scheduler
  outright (`app/api/settings.py`, range `0..1440`). The state was not shown late or shown
  wrong; it could not be measured from there at all. It is gone, and the
  `monitoring.auto_checks_waiting` string with it in all eight languages, replaced by
  `monitoring.auto_checks_disabled`. The header now answers from `check_interval` alone,
  through `autoCheckCadence()`: a cadence, "off" as a link to the one screen that switches them
  back on, or nothing. That third branch renders nothing on purpose — a header that cannot
  prove a state says nothing rather than guessing.

- **On a phone the monitoring table's card was 820px wide inside a 358px frame, and 476px of it
  were cut off with no scrollbar.** The card and the side column sit in a `grid`, where an
  item's `min-width` defaults to `auto`: the table's `min-w-[820px]` therefore climbed back out
  through the card and sized the grid item at 820px. `main` is `overflow-x: hidden`, so it cut
  the excess instead of scrolling it, while the table's own `overflow-x-auto` had nothing left
  to scroll and stood still. The filter row's right edge landed at 834px against a 402px
  viewport, reachable by no gesture at all. `min-w-0` on both grid children brings the card back
  to 326px and hands the overflow to the scroller built for it: 324 visible of 820, and nothing
  on the page out of reach.

  A second, quieter leak came out of the same measurement: `main` still reported 825px of scroll
  width against a 358px frame. It was not the table, which is clipped correctly. Tailwind ships
  `sr-only` as `position: absolute`, and the scroller was `position: static`, so the containing
  block of the two screen-reader spans in the last column was an ancestor above the clip: they
  sat at x=842 and pulled 467px of phantom scroll area into `main`. It was inert — no scrollbar,
  and keyboard focus is absorbed by the inner scroller — but a phantom scroll area parked behind
  an `overflow-x: hidden` is a horizontal scrollbar waiting for the day somebody removes that
  `hidden`. `relative` on the scroller makes it the containing block those spans were missing,
  and `/monitoring` now measures 358 = 358, like the `/services` table it was compared against.

- **The guided panel counted "Step 1 of 1" and armed a second primary button that finished
  nothing.** Two of the ten types the API serves ship a single guided step (adguard, traefik),
  and the panel still printed a step counter over one pagination dot whose only destination was
  the step already on screen. Below it sat "Finish", `variant="primary"` — the same blue as the
  footer's "Validate" 161 pixels down, and the one of the two that creates nothing: it moves the
  panel past its last step, where the field that names the integration is waiting. The counter
  and the dots now appear only from two steps up; the button is demoted to `outline`, so one
  blue button is left on the screen and it is the one in the footer; and
  `provider_modal.guided.finish` was renamed in all eight languages after where it actually
  goes, using the word each file already uses for `provider_modal.field.name`. The button
  itself was kept: nothing else reaches the naming step.

- **Confirmation dialogs opened with the destructive button armed on exactly the dangerous
  ones.** The opening focus was chosen from `variant === 'danger'`, read as a severity dial.
  It is not one: `danger` is what the harmless confirmations use, and `warning` is what the
  worse ones are built with — forcing an integration out while services still depend on it,
  deleting a domain that is in use, running a reconcile that writes to a live provider.
  Measured on a running instance, both pairs came out the same way round: deleting an unused
  domain opened on Cancel, deleting one in use opened on Delete; the first screen of an
  integration removal opened on Cancel, the escalated second screen opened on "Force
  removal". So Enter was safe on the question that changed nothing and destructive on the
  one that did. The rule is now "does confirming remove something": only `info`, which adds,
  opens on Confirm. `EXPECTED_FOCUS` in the new test is typed over the variant union, so
  adding a sixth variant without deciding this fails the build.

- **Choosing an integration type pre-filled the address field with the example address, and
  the example is a real machine on most home networks.** The type metadata carries a
  `placeholder_url`, which is also that field's placeholder — so the box held a value
  indistinguishable from the grey hint, being the same string. Nine of the twelve types name
  `http://192.168.1.10:3000`, the tenth address of the commonest home range, where something
  usually does answer. Anyone who read the box as already correct typed a username and a
  password beside it and pressed Validate, and the credentials were sent there. The rule now
  lives in `seedFormForType()`: a type change clears the URL, re-picking the same type keeps
  what was typed, and nothing else may put a value in it. The first-run wizard, which shares
  the same metadata and type picker, had only ever seeded the name.

- **The validation panel of both wizards answered in English inside a translated screen.**
  `providers.diag.detail.*` holds 41 translated sentences and `checkDetailText()` exists to
  pick them, but two of the three call sites printed the API's identifier for the check
  (`test_connection`) and its English sentence instead. The health line had the same shape,
  interpolating the wire value into a translated sentence to produce "État : healthy". Both
  wizards now go through `checkDetailText()` and a new `healthStatusLabel()`; the six status
  words were added to all eight locales by copying keys that already carried them, so no
  translation was invented.

- **Fifteen French strings and three more in pt/de had lost their accents**, all in the
  Settings panels and clustered in `settings.backup.*`: "Cles API" one line above "Clés API",
  "Backup versao {version}", "Webhook-Eintrage". Valid JSON, non-empty, invisible to every
  check in the build. `git blame` put the French ones in two commits four months apart, so
  the channel that eats them was still open. `NoWordLostItsAccentsTests` now closes it: the
  locale file is its own dictionary — every form it spells with diacritics, stripped, is
  searched again in the same file. Three filters keep it a spelling question and never a
  grammar one (a placeholder name and a URL are not prose; a diacritic on the final letter is
  a verb ending; under four letters is a function word), which leaves fourteen real
  homographs listed with their reason. It ships with its own controls: stripping "Clés API"
  must name that key, and `activé` beside `active` must stay silent.

- **`providers.type.npm.desc` described Nginx Proxy Manager as "Nginx Proxy Manager"** in all
  eight languages — the only one of the ten types whose description repeated its own name
  instead of saying what the tool does.

- **A service could name a second DNS server and a second proxy, and Vauxtra stored the
  choice without ever acting on it.** `extra_dns_provider_ids` and `extra_proxy_provider_ids`
  went into `service_push_targets` on create and on edit, the panel listed them, and the push
  never looked at that table: only the service's primary proxy and primary DNS provider were
  written. So the second DNS server held nothing, the drift report had nothing to compare, and
  the one guarantee the feature exists for — the hostname keeps resolving when the first
  server is down — was never true. `push_extra_targets()` now runs after every create and
  every edit, and its failures are reported the same way the primary's are (a `207` on create
  rather than a `201` that hides them).

- **Dropping a second target from a service left its records live on the provider.** The row
  disappeared from `service_push_targets`, so Vauxtra stopped seeing the record it had
  written, and the record went on answering. Renaming a service had the same shape: the old
  hostname stayed published on every extra target. `update_service` now withdraws from the
  targets it is about to drop — and from the old hostname when the FQDN changes — using the
  pre-update row, before it writes the new one.

- **The "services still depend on it" dialog said something false about half the list.** It
  claimed every dependent service would "stop being pushed anywhere", which is not what
  happens to a service that also names a second DNS server: that one keeps being published,
  exactly as before. `DELETE /api/providers/{id}` now returns `still_published` per dependent,
  and the dialog says which services go dark and which do not, with a tag on each row.

- **Deleting an integration in Vauxtra changed nothing on the integration.** Every record it
  already served stayed live on it, and Vauxtra lost the ability to see them, let alone remove
  them: the operator was left with a DNS server answering for hostnames no longer in any
  panel. The confirmation now carries a checkbox — ticked by default — that takes the records
  off the provider first (`?force=true&withdraw=true`), and a withdrawal that only half worked
  is reported as such instead of a bare "deleted" toast. The Integrations page and the setup
  wizard share one component for this dialog, so the two cannot drift apart again.

- **Four screens read an empty list as a fact about the panel.** A request that fails and a
  request that answers "there are none" both leave the same empty array behind, and the
  screens above them said the second thing either way. The worst of them was the last step
  of the setup wizard: when `POST /api/services/sync` failed, the toast saying so was gone in
  a few seconds and what stayed on screen was a green tick reading *No services found to
  import* — on the one page whose next button ends setup. Its own hint already admitted the
  ambiguity rather than resolving it ("or Vauxtra couldn't read them"), which is a sentence
  nobody can act on. The wizard now keeps the failure, says the scan could not be completed,
  says that finishing from here imports nothing, and offers the retry button that was
  already there. The hint drops its hedge, because it is no longer covering for two
  different facts.

  Three smaller ones, all of them sending somebody somewhere for no reason: a failed
  `/domains` told the expose form *No domain yet? Add one in Settings › DNS* — it now says
  the list could not be loaded and that a domain can still be typed in; failed `/tags` and
  `/environments` invited the operator to go and create their first one, each on its own
  flag, so the list that answered does not apologise for the one that did not; and the
  template modal did the same with its tag list.

- **The dependency audit could not tell a vulnerability from a registry that would not
  answer.** `npm audit --audit-level=high` exits 1 for both, and the CI step read nothing but
  that exit code. So a `400` from `registry.npmjs.org` — which happens, and happened here —
  turned a green branch red under the heading *npm audit*, with a failure that named no
  package and no version, and the only repair was to re-run the job until the registry felt
  better. Re-running a security gate until it passes is a habit worth not teaching anybody.

  `scripts/run_npm_audit.py` now decides from the shape of the answer rather than from the
  exit code: a real report carries `metadata.vulnerabilities`, and the script counts the
  severities at or above the level itself. Anything without that object is the registry
  declining to speak — retried, and if it still will not answer, the job fails saying
  exactly that: *nothing was audited*. It does not pass. An audit that never reached the
  registry has proved nothing about the dependencies, and a gate that goes green on silence
  is worse than one that flakes, because nobody re-runs a green job.

- **The dashboard had four ways of not saying "this failed".** Every widget on it decided it
  was loading by asking whether its data had arrived — `loading={!providersReady}`, where
  `providersReady` is `Array.isArray(providers)`. A request that fails leaves that array
  undefined for good, so a failed `/providers` in a fresh tab left the integrations card, the
  triage list and two KPI tiles pulsing skeletons **until somebody reloaded the page**. No
  error, no retry button, no end: the panel looked busy rather than broken, which is the one
  reading that suggests waiting is the right thing to do.

  Where a widget did notice and stop waiting, it said something worse. `NeedsAttention`
  builds its rows from the endpoint and integration lists, so with either of them missing it
  found nothing to list and announced **"All clear — every endpoint, integration and
  certificate is healthy"**, vouching for services it had just failed to read. The KPI tiles
  turned the same absence into a confident `0`, and the offline banner said *"Showing the
  last data received; it may be stale"* over a page where nothing had ever been received.

  A widget is now in one of three states rather than two. **Loading** means the request is
  still in flight. **Failed** means it came back with nothing: a dash and a stated reason on
  the tiles, an error with a Retry button on the integrations card, and on the triage list a
  notice that it is incomplete — which replaces "all clear" outright, because a list that
  could not read everything it triages has no business declaring the rest fine. The offline
  banner distinguishes a stale page from an empty one. Only when everything answered does a
  widget get to make a statement about the panel.

  Two figures deserved a mention of their own: "{enabled} enabled" and "{total} entries in
  total" are counted from a list with no `/stats` fallback, so a missing list used to print a
  zero underneath a headline number that was perfectly correct. They are now `undefined`
  rather than `0` when nobody answered, and the hint says so. The certificate tile had had
  this treatment since it was written, and its comment already said why: zero expiring and
  "we could not ask" look identical as a number, and only one of them means everything is
  fine.

- **Two dialogs open at once fought over the keyboard, and the one underneath won.** Every
  dialog in the panel — `Modal`, `Drawer`, `ConfirmDialog`, the command palette — listens for
  Escape and Tab on `document` in the capture phase, through `useModalDialog`. Two being open
  at once is more common than that sounds: a confirmation asked from inside a modal is the
  house pattern, and Ctrl/⌘ K summoned the palette over anything. All of them heard every
  key, and the one registered first — the one underneath — answered first. `stopPropagation()`
  never helped: it stops an event reaching further *nodes*, not the other listeners already
  attached to this one.

  So Tab inside a confirmation asked `container.contains(document.activeElement)` of the
  modal underneath, got false, concluded focus had escaped, and pulled it back out of the
  confirmation. On the confirmations that ask you to type a name before enabling their button
  — deleting a provider that still has services, resetting the panel, restoring a backup —
  there was no way from the field to the confirm button without a mouse. Escape reached both
  handlers and closed both, which with the unsaved-changes guard in front of a modal meant a
  single key could dismiss the guard and discard the form it was guarding in one stroke.

  `useModalDialog` now keeps a stack of the dialogs that are open, and only the one on top
  answers a key. Entries come off it by identity rather than by `pop()`: React makes no
  promise about which of two nested dialogs unmounts first, and closing the one underneath
  must not take the top one off the stack.

- **Ctrl/⌘ K opened the command palette over whatever was already on screen, and then
  navigated out from under it.** The palette exists to jump somewhere, so summoning it over a
  half-filled modal ended with that modal floating over a page it had nothing to do with, its
  Cancel button wired to a form the operator could no longer see. The global shortcuts now
  decline while a dialog is open — the `g` chords and `?` for the same reason — with one
  exception: Ctrl/⌘ K still closes the palette it opened, the single dialog it is allowed to
  answer over.

- **A failed background refresh of the security status threw away a password somebody was
  halfway through typing.** The security tab reads `/auth/me` through React Query, which
  refetches on window focus like every query in the panel. The error branch came first in
  the render, so a refetch that failed swapped the card — set a password, change it, or the
  environment-managed notice — for a full-width alert, and the retry then mounted a fresh,
  empty one. React Query keeps the last good data through a failed background refetch, so
  nothing was ever actually unknown: the form was discarded over an answer the panel already
  had. A blip behind a reverse proxy was enough, and the operator did nothing to cause it.

  A failure that still has data behind it is now reported *above* the form rather than in
  place of it, as a warning that says what survived: "Could not refresh the security status
  — what you have typed is still here". The alert is a sibling of the card rather than a
  branch around it, which is what keeps the card mounted: React reconciles a list of children
  by position, so rendering nothing in the alert's slot leaves the card exactly where it was.
  The full-width error card is still what you get when there is no data at all, which is the
  case it was written for, and both retry buttons now show their pending state while the
  refetch is in flight.

- **The health cycle held the only write lock SQLite has, across every network call it
  makes, and everything else that tried to write failed.** `run_health_checks()` opened a
  write transaction on its first `UPDATE` and did not commit until the end of the round.
  Between those two points it runs a TCP probe per service at three seconds each, a DNS
  provider's API per auto-updating service, an HTTPS fetch of every certificate, and up to
  twenty webhook POSTs. SQLite admits one writer at a time: for as long as all of that
  took, an operator saving a service, or the API disabling a host, waited out
  `busy_timeout` — fifteen seconds — and then failed with "database is locked".

  How long that had been happening in the open is written into the code: `_sync_npm_statuses`
  carries a branch that silently drops any error whose message contains that string, added so
  the logs would stop filling with it.

  The cycle no longer holds a transaction across a network call. Probes run with no
  connection open at all and their results are written in one short burst; every phase after
  that commits per item rather than per phase, so the lock is held for the length of a write
  and not for the length of a round trip. `_sync_npm_statuses` also asks each proxy for its
  host list once instead of once per service behind it — ten services on one NPM meant ten
  identical requests a cycle.

  Committing per item is also what keeps the work: under one transaction, a process that went
  down on the nineteenth webhook re-sent the eighteen before it, and an exception anywhere in
  the round discarded every status it had collected.

  Seven tests cover it. Five install a witness in the middle of a slow phase — a second
  connection, with a deliberately short timeout, that tries one small write and records
  whether it got in — and all five fail on the code as it was, with "database is locked",
  which is the defect reproduced rather than described. The other two say the round is still
  recorded and a tunnel service is still skipped, and pass on both sides.

- **Escape threw away a provider's credentials and a template's dozen decisions, the same way
  it used to throw away an exposure.** `ExposeModal` was fixed in 1.4.0; the two dialogs beside
  it were not, and they close on the same key for the same reason — `persistent` blocks the
  click on the backdrop and nothing else. In `ProviderModal` what is lost is a secret: a
  Cloudflare token pasted out of another tab, a proxy manager password. The form cannot offer
  it back, and neither can the provider's dashboard without issuing a new one. In
  `TemplateModal` it is a dozen separate choices — name, scheme, port, mode, three providers,
  domain, tags — unmounted together with the dialog body.

  Both now ask before discarding, and only when there is something to lose, so a dialog nobody
  typed into still shuts on the first press: a confirmation that fires every time is one people
  learn to click through. Cancel goes through the same gate as Escape, because the two ways out
  of a form should not behave differently. A save that succeeded still closes straight through
  — the server already has the form, and asking about it would be asking about nothing.

  The question itself now lives in `useUnsavedGuard`, rather than in a third and fourth copy of
  what `ExposeModal` worked out first. The two dialogs disagree only on what *dirty* means, and
  they have to: picking a provider type seeds `name` and `url` from the type's own metadata, so
  a form-versus-seed diff would call `ProviderModal` dirty the moment a tile is clicked — it
  tracks the two paths a human types through instead. `TemplateModal` compares against the
  template it was opened on, with the tag list sorted first, since tag order is click order and
  a tag turned off and back on is not a change anybody made.

- **The Docker import reported a container it had refused with the same number, the same
  colour and the same word as one it had passed over on purpose.** `POST /api/docker/import`
  answered `{"imported": n, "skipped": n, "errors": [...]}`, and `skipped` was a single integer
  standing for two outcomes that have nothing in common: a container Vauxtra already tracks,
  which is the ordinary result of ticking a whole page, and one this route *refused* — no
  address, or no usable port — which is the one case the operator has to go and fix. Neither
  was named and neither reached the journal, so a run that dropped five of six selected
  containers answered "5 skipped" and left no record anywhere of which five, or why. This is
  the defect `POST /api/services/import` was taken through one version ago, still standing in
  the other import.

  `skipped` and `errors` are now both lists of sentences naming the container Docker named,
  refusals are written to the journal as warnings, and containers passed over on purpose get
  one `info` line for the run rather than one line each. The panel reads all three: one toast
  per outcome, green for what was imported, neutral for what was already tracked, red for what
  was refused — the same three lines the migration panel already shows, in the same words,
  because they are the same three things happening to a different inventory. Before this,
  `errors` was not rendered at all: a run that refused every container it was given reported
  success.

  The two routes now report through one module, `app/importing.py`, rather than through a
  helper each. That is what stops the next fix from landing on one import and not the other,
  and it is asserted by identity rather than by resemblance.

- **A preflight check could say `blocking`, grey the save button out, and the update route
  would save that exact body anyway.** `blocking: True` is not a severity in this codebase, it
  is a claim about another route: the Expose panel disables "Create route" while
  `summary.blocking_failures` is above zero and offers nothing to press instead, so a check
  that blocks is promising the save route refuses the same body. Measured on a service whose
  DNS provider was kept and whose address was cleared, `POST /api/services/preflight` answered
  `{"blocking_failures": 1, "ok": false}` with `dns_target_resolution` failing on
  `dns_target_required`, `POST /api/services` answered `400 dns_target_required` on the same
  body — and `PUT /api/services/1` answered `200` with `errors: []`. The saved service kept
  serving, because after resolution had already failed `update_service` fell back to
  `old["dns_ip"] or ""` and wrote a DNS rewrite pointing at whatever address the row happened
  to be holding. The operator saw a red badge, a dead button, and a service that saved and
  worked; three routes, three answers, on one body.

  A second, narrower gap sat next to it. `add_service` opens by refusing a `proxy_dns` service
  with no provider target at all; `update_service` never had that guard. Measured on
  `{proxy_provider_id: null, dns_provider_id: null}`: preflight `{"blocking_failures": 1,
  "ok": false}` on `provider_target_required` / `target_none`, `POST` `400 At least one proxy
  or DNS provider target is required`, `PUT` `200` — the hostname moved and nothing anywhere
  was left to serve it. `update_service` now raises the same 400, after the 404 so that a PUT
  on a service that is not there still says so first.

  The third asymmetry pointed the other way, and cost the operator a refusal they did not
  deserve. `_run_preflight` always resolved the public target from nothing, which is what
  `add_service` does; `update_service` resolves from the row it is about to overwrite. A
  service already holding a target, in automatic mode, on a network where detection found
  nothing, therefore read `dns_target_detection_failed` and `{"blocking_failures": 2,
  "ok": false}` while the `PUT` on the same body kept that very target and answered `200` —
  a greyed-out button in front of a save that would have succeeded. The preflight now reads
  the stored row when the body carries `service_id`, which is what the panel sends when it is
  editing, and answers `dns_resolved` with `{"resolved_target": "203.0.113.9", "source":
  "current"}` and `{"blocking_failures": 0, "ok": true}` for that case.

  The blanket `old["dns_ip"]` fallback is gone rather than mirrored into the preflight. A
  detection blip is already absorbed one layer down: the update passes the stored address to
  `resolve_public_target` as `current_value`, and `current` is a ranked candidate like any
  other under the priority policy in Settings. The fallback ran after that policy had spoken,
  so an operator who had deliberately removed `current` from their priority list got it back
  anyway, silently, on every save.

  **Upgrading:** a `PUT /api/services/{sid}` that used to be accepted is now refused with the
  same 400 the creation gives, in two cases: a `proxy_dns` service edited down to no provider
  target at all, and a DNS provider kept with no resolvable public target. A client that
  relied on the stored address being silently restored must send an address, or switch the
  service to automatic detection with `current` present in the public-target priority policy.

- **A check that blocked for a good reason was named in no doctrine list, and the guard meant
  to catch that could not see it.** The `provider_missing` branch of `_check_provider` is
  legitimately blocking — `_unknown_references` answers `400 Nothing was created -- unknown
  provider 404` for a primary provider id pointing at nothing, and the same for an extra one —
  but it appeared in neither of the two doctrine lists in `app/api/services.py` and was
  excluded from `_MAY_BLOCK` in `tests/test_preflight_symmetry.py`. Both now name it. More to
  the point, `_MAY_BLOCK` is no longer keyed on the check name. `proxy_provider` and
  `dns_provider` each carry four branches and two of the four land on opposite verdicts, so a
  name-level list cannot say which one it is approving; it is now a set of eleven
  `(name, detail_key)` pairs, and a new branch that blocks without being declared fails the
  suite instead of being absorbed by its neighbours.

- **The symmetry suite only ever drove the creation route.** `tests/test_preflight_symmetry.py`
  measured every corpus case through `add_service` and nothing else, which is how three
  measurable asymmetries lived under a green suite. Each of the 17 cases is now driven through
  both `POST /api/services` and `PUT /api/services/{sid}` from one route table, and a guard
  test fails if a case is not measured against every route — so a case added later covers both
  by construction. The file went from 12 tests and 14 subtests to 18 tests and 34 subtests.

- **`apply_template` invented a port when neither the call nor the template named one.**
  `POST /api/services` requires a domain and a port; a template is allowed to carry neither,
  which is what lets one template serve several domains. The bridge tool filled both gaps with
  `or 80` and `or ""` before sending. The empty domain came back as a 422 and the operator saw
  it; `80` did not, because 80 is a valid port. Every call that named no port, against a
  template that sets none, created a service pointing at a port nobody had chosen — and
  reported success. Both masks are gone: when neither side supplies a value the tool refuses
  and sends nothing, naming which one is missing.

- **A duplicate that arrives during the write is answered again, instead of crashing the
  route.** `POST /api/docker/endpoints`, `POST /api/environments` and `POST /api/tags` each
  ask for the duplicate before they write it, which is what produces the sentence an operator
  can act on. But a SELECT followed by an INSERT is two statements, not one atomic step: two
  calls carrying the same key both pass the lookup on a table that does not hold it yet, the
  second INSERT meets the UNIQUE index, and the `sqlite3.IntegrityError` that nobody caught
  reached the caller as `500 Internal Server Error` — the server announcing that it had broken
  over a conflict it has a word for. Measured on all three routes by letting a second
  connection commit the same key between the lookup and the INSERT: **500 before, 409 after**,
  with the same sentence the lookup gives. The lookup stays, and it is still what answers the
  ordinary duplicate; the new handler catches `sqlite3.IntegrityError` and nothing wider, so a
  locked database, a missing column and a full disk — all `sqlite3.OperationalError` — keep
  leaving as our own 500 rather than being renamed duplicates, which is the bug the lookup was
  introduced to end.

- **`GET /api/docker/containers` stops blaming the Docker daemon for Vauxtra's own bugs.** The
  `try` in that route carried a comment saying it wrapped the remote call. It wrapped roughly
  thirty lines: `_extract_container_port`, `_extract_container_ip` (`app/api/docker.py`) and
  `analyze_container` (`app/services/docker_analyzer.py`) all ran inside it, and every fault in
  them was rewritten as `502 Failed to list Docker containers: …`. 502 means "I asked someone
  else and what came back was not usable", so a `TypeError` of ours arrived as an accusation
  against the operator's Docker host, with a sentence sending them to inspect a daemon that had
  just answered correctly. Measured by injecting a fault into each of the three helpers in
  turn: **502 before, 500 with our traceback after**. The guard now covers only the statements
  that put a request on the socket — `containers.list()`, and the read of `Container.image`,
  which docker-py resolves lazily through `client.images.get()` on the same daemon — so a
  daemon that refuses either of them is still a 502, and everything in between is ours.

- **One container with a deleted image took the whole discovery list down.** docker-py returns
  `None` for `Container.image` when the image id is gone — an image removed while its container
  kept running, which Docker allows — and the expression building the listing fell through to
  `image.short_id` in exactly that case. The `AttributeError` left as a 500, so every other
  container on that host disappeared from the discovery panel behind one unusable row. It is
  now the one row with a blank image name.

- **A restore told you two thirds of what it had done.** `POST /api/restore` has always
  answered with `settings_not_restored` and `domains_without_name` alongside
  `webhooks_needing_url`, and both panels that call it declared their own narrow response type
  — `{ ok, webhooks_needing_url? }` in the settings card, a four-field inline type in the setup
  wizard — so TypeScript agreed the other two fields did not exist and nothing was there to
  print them. Neither is a failure: the restore succeeded. They are the two things the file
  could not bring back, and nothing else in the panel ever mentions them. A setting this
  version does not accept is simply absent afterwards, with the old value gone and no row to
  show it was ever there; a domain the file carried without a name is not recreated, so every
  service and route that pointed at it now points at a domain the list no longer offers. Both
  components now consume the `RestoreResult` type the API module already declared, and both
  report all three outcomes in the idiom of the screen they live on: the settings card adds two
  neutral toasts beside the existing webhook one — no red, because nothing failed — and the
  setup wizard adds two warning panels to the summary it already shows, which is the operator's
  only chance to read this at all, since the wizard leaves for the dashboard straight after.
  The dropped setting names travel with the sentence; a count with no names would only tell
  somebody that something is missing.

- **A setting could be saved and never take effect, and the screen said only "saved".**
  `POST /api/settings` answers with `not_applied` when a value was written but the running
  process could not be told — rescheduling the health check or the reconciler raised — and
  `GeneralTab.tsx` read `saved` and `ignored` and dropped the third. The operator saw a green
  toast, watched the interval not change, and had nothing connecting the two. It now renders
  as a warning that names the keys and says a restart applies them, which is the only line on
  that screen that asks for something to be done.

- **The guard against blaming ourselves for somebody else's refusal recognised one of the six
  shapes it claims to catch, and the three files it swept were a list somebody typed.**
  `tests/test_upstream_failures.py` walks the AST looking for a 500 or a 503 raised over a call
  that left the process. It found the carrier by asking what built it, from a fixed set of six
  factory names, which is a rule that only ever sees the code that existed when the set was
  written. Measured against six recidive shapes: it flagged one. An `httpx.Client` or a
  `requests.Session` held in a variable, a factory nobody had added to the set, and a call
  relayed through a second local name (`target = provider`) all walked past it — and a bare
  `_PARSE_ONLY = {"add"}`, written for apprise's URL parsing, exempted `add` on *anything*, so
  `provider.add(record)` — a write to a DNS zone — was waved through without a word.

  A name is now a carrier when what built it ends in `client`, `session` or `provider`, so a
  factory written next year needs no entry anywhere; when it is relayed from another carrier;
  or when it is bound by `with`, by `:=` or by an unpacking rather than a plain `=`. A verb on
  `httpx`, `requests`, `docker`, `apprise` and five other wire modules counts with no variable
  in front of it at all. The exemption is now keyed on the carrier and not on the method name:
  `add` is parse-only *on an apprise bag*, which is the one place where it instantiates a
  plugin locally and sends nothing. `connection` is deliberately absent from the factory words,
  because `get_connection()` in `app/models.py` hands back a SQLite handle on a local file;
  counting it put every `conn.execute` in `app/api/` under a rule about somebody else's
  refusal, and a detector that flags the database is a detector nobody reads.

  Every one of the eight shapes now has a snippet the detector must flag, asserted one per
  subtest, because a rule that matches nothing passes an empty sweep for free. Two negative
  controls sit beside them: our own client construction, and the local database handle. The
  three-entry `_GUARDED` tuple is gone; the sweep reads every module under `app/`, discovered
  rather than listed. Measured over the whole directory: 48 of its 258 try blocks reach
  somebody else, spread over 9 files, and 0 of them answer 500 or 503. A second test counts
  those 48, so a change that makes the carrier rule blind fails loudly instead of leaving the
  sweep green over an empty question.

- **Four sentences described code that no longer does what they say.** Each was re-measured
  before it was rewritten.

  `docs/DEPLOYMENT.md` and `docker-compose.yml` told an operator that dropping the Docker
  socket mount leaves "the Docker screens answer 503". An unreachable daemon has answered 502
  since `app/api/docker.py::_docker_client` stopped blaming this server for a socket that was
  never mounted; measured against a dead `tcp://` host, the route answers `502 Docker daemon
  unavailable`. The documents and the assertion in
  `tests/test_provider_ids_and_secrets.py::test_the_way_to_spend_less_is_written_down` moved
  together, and that assertion now pins the sentence rather than three digits that could match
  anything else on the page.

  `tests/test_regressions_v2.py` and `app/models.py` both said `delete_webhook` was "one
  `DELETE FROM webhooks WHERE id=?` and nothing else" — "the whole body". It has not been that
  since the route learned to answer 404: it looks the id up, refuses a missing one, deletes,
  commits and closes. What is actually load-bearing for the cascade is narrower and still true,
  and is what the prose says now: that one `DELETE` is the only statement in the route that
  removes anything, and nothing in it reaches `webhook_delivery_log`, which is why the queue
  has to leave with the row by itself.

  `frontend/src/components/features/expose/ExposeModal.tsx` and
  `tests/test_preflight_symmetry.py::TheCheckListIsKeyedOnMoreThanTheNameTests` both said React
  keeps the first of two siblings sharing a key and drops the second, which is why a second
  spare target "disappears" from the preflight panel. Measured on React 19.2.8: both `<li>`
  render. Nothing disappears. React logs `Encountered two children with the same key`, says
  such children "may be duplicated and/or omitted", and calls the behaviour unsupported and
  liable to change. What a shared key actually costs is identity, which is the only thing a key
  is for: with one key for two rows the reconciler fell back to matching by position, so
  swapping the two lines left each row's state and DOM node sitting on the other line, and a
  later change of keys left a stale third `<li>` standing for two lines of data. The key change
  itself was right and stays; only the reason given for it was wrong.

- **Three copies of the scope vocabulary, and nothing comparing them.** `VALID_SCOPES`, the
  `Literal["read", "write", "admin"]` on `ApiKeyCreate.scopes` (both in `app/api/api_keys.py`)
  and the keys of `_SCOPE_LEVEL` in `app/auth.py` each write the same three words, and only
  the last decides anything at request time. Measured: the three agree today, so this is a
  guard rather than a repair. What it guards is asymmetric and silent in both directions. A
  scope that creation accepts and the ladder does not know is read as level -1 and authorizes
  nothing, so the key is minted, listed, and useless. A scope the ladder knows but creation
  refuses can never be granted at all, so a route gated on it is unreachable. Neither raises.
  `tests/test_api_key_scope_vocabulary.py::TheThreeScopeListsAgree` now compares all three as
  sets, and the failure message names the file and the symbol that moved rather than printing
  two sets and leaving the reader to work out which is the odd one. Witnessed against four
  deliberate drifts, including the one where the annotation is rewritten and the reader
  quietly answers the empty set.

- **One screen, two names, and neither the tab nor the sidebar used the one the page answers
  to.** The settings tab strip (`frontend/src/components/features/settings/tabs.ts:49`) and the
  sidebar submenu (`Sidebar.tsx:212`) both render `settings.tab.logs`, which read "System Logs";
  the page those two open renders `settings.logs.title`, which reads "Action Logs", above
  `settings.logs.desc`: "Everything Vauxtra did, newest first." `GET /api/logs` reads the `logs`
  table, the one `add_log` writes to, so the page is right and the two entry points were
  promising something the product does not have — there is no system or daemon log viewer
  anywhere in it. Both entry points now carry the page's own title, taken per file from that
  file's `settings.logs.title` rather than translated again: "Journaux d'actions",
  "Aktionsprotokolle", "Registros de acción", "Actielogs", "Logs de ação", 操作ログ, 操作日志.

- **A sentence about several notification targets kept referring back to one of them.**
  `settings.backup.restore_webhooks_disabled_other` says how many targets came back from a
  secretless backup without their URL, and then in the same breath said to re-enter *it* and
  switch *them* back on. English, French, Spanish, Dutch and Portuguese all carried some form of
  it: `does not carry it. Re-enter it`, `ne la contient pas. Ressaisissez-la`, `no la incluye.
  Vuelva a introducirla`, `Voer de URL opnieuw in`, `não o contém. Introduza-o`. German was
  already right, because *sie* is both the feminine singular and the plural; Japanese and
  Chinese do not mark number here at all. Each replacement is a word this file already uses:
  `setup.restore.done_webhooks` tells the same operator the same thing about the same objects at
  the end of the restore wizard, and has said it in the plural in every language all along —
  "Re-enter them", "Ressaisissez-les", "introducirlas", "URL's". Nothing new was translated.

- **The Spanish and Portuguese import summary called a route masculine.** `ruta` and `rota` are
  feminine in both languages, and `settings.migration.discovered_one` / `_other` put the same
  unchanging masculine participle in front of both forms: "Descubierto: {count} ruta" and
  "Descubierto: {count} rutas", "Descoberto - {count} rota" and "Descoberto - {count} rotas" —
  wrong gender at one, wrong gender and wrong number at many.
  They now read "Descubierta" / "Descubiertas" and "Descoberta" / "Descobertas". Both files
  already inflect this exact participle one screen over, in
  `settings.docker.toast_discovered_one` / `_other` ("{count} contenedor descubierto" /
  "{count} contenedores descubiertos"), and French already inflects this very key. German
  *Entdeckt* and Dutch *Ontdekt* agree with nothing and are untouched.

- **The setup summary chose between two labels that were the same label.** `DoneStep.tsx`
  rendered `skipPassword ? t('setup.done.summary_open') : t('setup.done.summary_password')`, and
  those two keys held byte-identical values in all eight locale files — "Panel access", "Accès
  au panneau", パネルへのアクセス. Whichever way the branch fell the screen said the same thing,
  so the branch had never shown anything and could not have been noticed by looking. The two
  keys are replaced by one, `setup.done.summary_access`, named after its row the way its three
  neighbours are (`summary_providers`, `summary_webhooks`, `summary_docker`). What `skipPassword`
  actually changes on that row — the value beside the label, the lock icon and the tone — was
  already correct and is unchanged.

- **Every check on this repository attested to a runtime the published image does not have.**
  `build(deps): bump the docker group` (834dfc3) moved the Dockerfile from `node:22-slim` and
  `python:3.13-slim` to `node:26-slim` and `python:3.14-slim`, and it touched that one file —
  one file changed, two insertions, two deletions. `tests.yml` went on installing Python 3.13
  and Node 22, and so did both jobs in `security.yml`. From that commit the suite, the lint,
  the dependency audit and the vulnerability scans all ran on a runtime the image does not
  ship, and the image is built, signed with cosign and given a SLSA provenance attestation on
  the strength of exactly those checks. Gating the publish is what makes that signature worth
  what people read into it; gating it with a different interpreter quietly gave the words back.
  All five pins now follow the Dockerfile.

- **The SBOM that ships beside the image was resolved on a different interpreter than the
  image.** `security.yml` installs the dependency set before running Syft, because Syft's
  `requirements.txt` parser drops every line whose constraint is not an exact pin and all
  thirteen lines here use `>=` — without that step the SBOM carried zero Python packages. That
  resolution ran on Python 3.13. Wheel availability and environment markers are decided per
  interpreter, so the published SBOM, and the Grype scan that is the only step in this pipeline
  allowed to break the build, were both computed against a dependency set that need not be the
  one inside the image. The step now resolves on the interpreter the image ships.

- **Three sentences described a Dockerfile that had changed underneath them.** `README.md` told
  a reader the build uses "Node 22 for the frontend, Python 3.13-slim for the final image". The
  comment introducing the `docker` entry in `dependabot.yml` said the Dockerfile pins
  `node:22-slim` and `python:3.13-slim`. And the comment in `security.yml` that justifies
  scanning the built image did so by naming the userland it scans, `python:3.13-slim`. That
  last one was the hardest to see: the tag was split across two comment lines mid-token, so a
  grep for it found nothing and the first version of the gate below could not reach it either.
  All three were true the day they were written and false the moment the bump landed, and all
  three now name the images the Dockerfile actually uses.

### Changed

- **`POST /api/settings/api-keys` answers `201`, not `200`.** The eleven other routes that
  create a resource already answered `201`; this one did not. Nothing broke, because both the
  panel and the MCP bridge accept any 2xx, which is exactly why it went unnoticed for so long.
  It was wrong in the one place that matters least to us and most to everyone else: the
  published OpenAPI schema, which is what a third-party client reads to learn what a
  successful creation looks like. A test now holds every creation route to `201` as declared
  in that schema, and holds the list of those routes against the schema in both directions so
  it cannot rot unnoticed.

  **Upgrading:** a client that compares the status to `200` exactly must accept `201`. Both
  clients shipped with Vauxtra already accept any 2xx and need no change.

- **Forty-five routes sit behind the `write` scope; six were watched, by a list somebody had
  to remember to extend.** `tests/test_api_key_scopes.py` drives a write key through five
  paths written out by hand plus the single-service check, and asserts only that the answer is
  not 403. The gap showed up in the measurement behind the security entry above: a key whose
  column read `'read, write '` moved from 403 to 200 on forty-two routes when segment stripping
  was added, and nothing in the suite would have noticed. Creating a service, deleting a
  provider, bulk-disabling everything, importing from Docker: all of them moved without an
  assertion watching. `tests/test_api_key_write_scope.py` no longer keeps a list. It walks the
  application's own router, follows the `_IncludedRouter` wrappers FastAPI 0.141 leaves in
  `app.routes`, reads the scope out of each endpoint's source because the gate is a call in
  the body rather than a dependency, and synthesises the smallest body the route's model will
  accept so a 422 cannot be mistaken for a decision. A route added next month is swept the day
  it is added. Both directions are pinned on all forty-five: a read-only key is refused on
  every one, a write key and a padded `'read, write '` key are accepted on every one. And
  because a sweep over an empty list would pass every assertion in the file, the walk is
  witnessed before it is trusted. `POST /api/services/bulk` is then driven for real, with no
  stop and no patching, and the row it disables is read back out of SQLite rather than out of
  the response body.

- **The MCP bridge refuses a bad value instead of forwarding it.** `forward_scheme`,
  `expose_mode` and `public_target_mode` are declared `Literal` on the tools that take them,
  and `target_port` is bounded to 1–65535, matching the models the routes already validate
  against. FastMCP reads those signatures, so a wrong value is now refused at the call with the
  allowed set in the message, rather than after a round trip as a 422 the assistant has to
  interpret. `bulk_service_action` carries its verb set the same way. The tool docstrings state
  each constraint in prose beside the declaration, and the parity checker above fails the build
  if the two descriptions of the same rule ever disagree.

- **A comment justified a field constraint with something measurably untrue.** The minimum on
  `ApiKeyCreate.scopes` was explained by "only something that sees the list itself can refuse
  an empty one", and a `field_validator` sees the list itself: it is handed the whole of it.
  Measured side by side, a model refusing `[]` from the field and one refusing it from a
  validator both refuse it. The real difference is where the refusal lands. Only the field
  constraint reaches `model_json_schema()`, where the `scopes` property reads `{"default":
  ["read"], "items": {"enum": [...]}, "minItems": 1, "type": "array"}`; move the refusal into
  the validator and the property is the same document with the `minItems` line gone. That
  document is what `tests/test_api_key_scope_residues.py::BridgeSignatureParity` compares
  against the MCP bridge's own tool parameters, so a constraint the schema cannot carry is a
  constraint that comparison cannot see. The comment now says that, and the measurement behind
  it runs as a test instead of sitting in prose.

- Five test files carried French names in a repository whose code, comments and tests are
  otherwise English: `test_api_key_scopes_vides.py`, `test_erreur_de_la_destination.py`,
  `test_import_silencieux.py`, `test_reponse_non_mesuree.py` and
  `test_restauration_silencieuse.py` are now `test_api_key_empty_scopes.py`,
  `test_upstream_failures.py`, `test_import_outcomes.py`, `test_unverified_answers.py` and
  `test_restore_reporting.py`. Their contents are unchanged.

- **The em dash leaves the French interface.** Thirty-one strings in `fr.json` carried an em dash,
  thirty-three of them in all, and `—` is a mark French typography does not set in running text:
  `Connexion refusée — vérifiez le nom d'utilisateur`, `7 jours ou moins — à renouveler
  maintenant`, `Ne partagez jamais ce jeton — il donne un accès en écriture`. Each now takes the
  mark French sets there. Twenty-eight become a colon, where the second half explains the first.
  Three become a comma, where it merely qualifies it: `Enregistrez, puis utilisez`, `IP ou nom
  d'hôte uniquement, pas de suffixe /admin`, and `listées, le jeton a besoin de Zone:Read`, that
  last one to keep a second colon three words away from `Zone:Read`. Two become a small dash, in
  the lines that already end on a colon — `Option A - Non sécurisée (test rapide) :` — which is
  also the mark `settings.migration.discovered_*` has always used in this file. `fr.json` now
  holds no `—` at all. No wording changed and no placeholder moved; the other seven files keep
  their own punctuation, because this is a claim about French, not about the strings.

- `settings.docker.endpoints_load_failed` and `settings.docker.endpoints_load_failed_hint` move
  up to sit after `settings.docker.endpoint_test_failed`, where they sort, in all eight files.
  They had been appended between `no_endpoints_desc` and `no_port` — where the writer happened
  to be reading, not where the key belongs. Measured across the whole file, they are the only
  break in alphabetical order anywhere under `settings.docker`, so they were the one pair a
  reader scanning that family in order could not find.

### Removed

- `settings.docker.import_done`, in all eight locales — the single green line reporting
  "{imported} imported, {skipped} skipped" for the Docker import. It is replaced by
  `settings.docker.import_success`, `settings.docker.import_skipped` and
  `settings.docker.import_errors`, which are the three outcomes the route now distinguishes,
  each a plural family so the sentence agrees at one as well as at many.

- `expose.preflight.detail.dns_unresolved`, in all eight locales. No preflight check has
  emitted that detail key since the DNS target gate was rewritten to answer `dns_resolved`,
  `dns_target_required` or `dns_target_detection_failed`; nothing in `app/`, `vauxtra_mcp/` or
  `frontend/src/` names it. (`monitoring.drawer.dns_unresolved` is a different key, and stays:
  the service drawer still renders it.)

- `setup.done.summary_open` and `setup.done.summary_password`, in all eight locales. They held
  the same value as each other in every file, and `setup.done.summary_access` replaces both;
  the ternary that chose between them is written up under **Fixed** above.

---

## [1.4.0] — 2026-09-11

### Security
- **The example addresses in the interface were real hosts, and they were in the published
  history.** `expose.field.dns_target_local_placeholder` and `expose.field.target_placeholder`
  shipped a maintainer's actual LAN addresses in all eight locale files, and one test fixture
  carried a third. They looked like placeholders, which is exactly why nobody caught them —
  a host address only reads as private if you already know the network. The strings were
  replaced in the working tree earlier in this cycle, but a public repository publishes its
  history too: they were still readable in every commit since the interface rewrite, and in
  the `v1.3.0` tag. The whole history has been rewritten to replace them with the
  documentation subnet, and `scripts/check_repo_hygiene.py` now refuses any RFC 1918 or
  link-local literal outside a declared allowlist, so the next one fails CI instead of
  shipping.

  Anyone holding a clone from before this rewrite still has the old objects. The published
  commit identifiers have all changed as a result; a fresh clone is the only clean copy.

### Added
- **The image is now built and scanned on every branch, and shipping pip is what that
  caught.** Nothing in this repository ever ran `docker build` outside the publish step, so
  a Dockerfile that does not build surfaced on a tag — after tests and the security scan had
  both gone green, with the version already public in git and no image to go with it. And
  every scanner here read the *source tree*, never the artifact: `python:3.13-slim` is a
  Debian userland, so an openssl or zlib advisory landed in the shipped image without
  touching a line of this repository and nothing said a word. A new `image-scan` job builds
  `linux/amd64` and runs Trivy over the result. One platform on purpose: it proves the build
  and scans the userland, both architecture-independent, and shares its layer cache with the
  multi-arch publish.
  Its first run found two HIGH, and both came from `pip` rather than from `requirements.txt`
  — msgpack `GHSA-6v7p-g79w-8964` and setuptools `CVE-2025-47273`. Neither package is
  installed in the image. Both are lines in `pip/_vendor/vendor.txt`, the manifest pip
  carries for its own dependency tree, which no scanner can tell apart from a real install
  list; the setuptools module the CVE is about is not even shipped. Nothing was pinnable, so
  pip is removed after the install instead, along with the ensurepip wheel that is a full
  copy of it. That drops both findings, takes a working package installer out of a
  network-facing container, and saves 7 MB. The Debian layer was clean.

### Fixed
- **Escape threw away a fully configured exposure, with no undo and no trace.** The wizard is
  opened with `persistent`, which reads as "this one cannot be dismissed by accident" — but
  `persistent` only blocks the click on the backdrop. Escape still reached `handleClose`, and
  `handleClose` resets all seven pieces of state at once: hostname, target, the selected
  providers, tags, environments, the preflight that was just run. Several minutes of work, gone
  to one key pressed to dismiss something else — an autocomplete list, an OS notification.
  Closing now asks first, and only when there is something to lose: the form is compared
  against the state it was seeded with, so an untouched wizard still closes on the first press.
  The `done` step is deliberately never dirty — the push has already happened and that panel is
  a receipt. Cancel goes through the same gate as Escape, so the two ways out behave alike.

  `ConfirmDialog` is portalled to the body for this, as `Modal` and `Drawer` already were.
  Rendered in place it was earlier in the document than the modal's own portal, at the same
  `z-50`, so a confirmation asked from inside a modal painted underneath it — and `fixed
  inset-0` would have been measured against the modal panel's `zoom-in-95` transform rather
  than the viewport.
- **Deleting a webhook did not stop the sends already queued to it.** `delete_webhook` is one
  `DELETE FROM webhooks WHERE id=?` and nothing else, and `webhook_delivery_log.webhook_id`
  named a webhook without referencing one — so the webhook went and its retry queue stayed. The
  retry job reads the destination off the log row rather than off `webhooks`, which means
  Vauxtra kept POSTing to a URL the operator had just revoked, for the full length of the
  backoff: up to twenty-four hours later. A Discord token pulled *because* it had leaked was
  still being used the next day. Schema 11 adds the cascade so it cannot happen again, and the
  migration's rebuild drops the rows it has already happened to — no cascade reaches backwards.
  Rows with a NULL `webhook_id` are kept: an ad-hoc send has no parent webhook and is not an
  orphan. The rebuild keys off the pragma rather than the version row, so an install whose
  version was written by a failed earlier attempt is still repaired, and `foreign_key_check`
  runs before the COMMIT so a database that still violates the constraint keeps its old table
  instead of a half-built one.
- **Four screens turned a failed request into a factual claim about the operator's
  infrastructure.** A list read as `data ?? []` has the same shape whether the backend answered
  "nothing" or did not answer at all, and every one of these sites rendered the first reading:
  "No webhook configured" with an invitation to create one, "No integration type available"
  (which reads as a broken build, not a network blip), "No Docker engine configured" with an Add
  button, "No check in the last 24 h" on hosts the scheduler had probed every cycle, and an
  availability figure quietly replaced by an em dash. The Monitoring drawer said the same thing
  twice more, on its timeline and its logs tab. The damage is not cosmetic: the operator acts on
  those sentences — re-adding an endpoint that already exists, or chasing an uptime gap that
  never happened. Each of the four now distinguishes the two cases, says which request failed,
  and offers a retry; where a retry has no place — a drawer tab, a table cell — it states that
  the figure is unknown rather than zero. The hooks return their query alongside its rows so the
  caller can tell the cases apart at all, which is what `StepTypeSelector`'s existing comment
  about the loading case already argued for. A service behind a tunnel keeps its own explanation:
  its history is empty by design, whether or not the request succeeded.
- **Testing a second integration stole the first one's spinner.** The Integrations page tracked
  each action with a single `number | null`, which cannot name two rows at once. Clicking Test
  on one row and then another overwrote the id, so the first row stopped spinning and its
  button re-enabled while its request was still open; and whichever response returned first
  cleared the tracker unconditionally, so the second row lost its spinner while still working.
  The diagnostics badges then updated out of order and a result could be read against the wrong
  integration. `toggling` was the worst of the four: it drives `disabled` on the enable/disable
  switch, so a stolen spinner made a provider clickable in the middle of its own write. Each
  action now tracks a set of ids — the shape `actioningIds` already uses on the Services page,
  which never had the bug — and marks busy in `onMutate` so the pairing with `onSettled` is
  explicit: both receive the same row, and only that row is released.
- **Saving one Settings card discarded what was typed in the other three.** The four cards on
  the General tab share one mutation whose `onSuccess` invalidates `['settings']`, and they
  shared a single remount key joined over all ten server fields. Saving any one of them
  refetched, one field changed, the joined key changed, and all four remounted — and since each
  card seeds its fields in a `useState` initialiser, a remount is a hard reset to the server
  value. Paste three new sources into the WAN policy, scroll up, save the health checks, and
  the paste was gone with no toast and no warning. One key per card, over that card's own
  fields, which is what the comment above it already promised.
- **The whole app painted raw translation keys on its first frame.** `t()` falls back to the
  key itself when it is missing, and the translation map arrives asynchronously after mount.
  The provider wraps the entire tree, so the window covered every screen rather than only the
  lazy routes — and `App.tsx`'s `Suspense` fallback guarded nothing, since it renders
  `{t('ui.loading')}` and was part of the bug. On a slow link or a cold CDN the operator saw a
  sidebar reading `nav.dashboard`, `nav.services`, `nav.monitoring`: it looks like a corrupted
  build, and the natural reaction is to reload or roll back a perfectly healthy release.
  Non-English users hit it on every cold load, their locale chunk never being the one already
  parsed. Rendering now waits for the first map. `isLoading` could not gate it — that goes
  true again on every language switch, and blanking the app mid-session would be worse than
  holding the previous language until the new one lands — so the gate is one-way and reads the
  cache rather than waiting on it. The hold screen carries no text by construction: any label
  would need a translation that is not loaded yet.
- **Four tables outlived a restore.** A restore replaces the instance: it empties the tables,
  then re-inserts the backup's rows under their original ids. A table left out of that wipe
  therefore does not keep orphans — it keeps rows that now name a different record.
  `webhook_delivery_log` was the worst of them: it has no cascade from `webhooks`, and the
  retry job reads a delivery's destination off the log row rather than off `webhooks`, so a
  queued send kept firing at a webhook the restored set does not contain, for the full 24 h of
  backoff. `scheduler_state` kept alert bookkeeping keyed by `(service_id, webhook_id)`, and
  `service_templates` — exported by neither backup route — came back with its provider columns
  blanked by the cascade and its `tag_ids_json` naming someone else's tags, so the next service
  created from a template landed on the wrong tags with no provider attached. `uptime_events`
  was already emptied by its cascade on `services`; it is listed anyway so the wipe does not
  depend on a pragma being on. `api_keys` stays out on purpose: only a key's prefix is ever
  exported, never its hash, so wiping it would lock the operator's own automation out of the
  instance it just restored. The list is now a module constant held to the schema by a test, so
  a table added later fails a test instead of silently surviving every restore.
- **Three legitimate spellings of one origin matched nothing.** `CORS_ORIGINS` is compared to
  the browser's `Origin` header character for character, so a rebuilt origin that is merely
  *equivalent* is a rejection — and a silent one: the request fails in the browser while the
  log says `CORS origins validated`. A trailing slash, which is what the address bar shows and
  therefore what gets pasted, refused the whole setting; one bad entry refuses them all, so a
  second, perfectly good origin went down with the first. An explicitly written default port
  was kept, and `Origin` reads `https://host`, never `https://host:443`. And an IPv6 literal
  came back from `urlparse` stripped of the brackets that make it an authority, so
  `http://[::1]:8888` was stored as `http://::1:8888`. All three are normalised to the exact
  string a browser sends now, and two spellings of one origin count once. Anything that is not
  an origin — a path, a query, a fragment, a wildcard, an unknown scheme, a port out of range —
  is still refused.
- **Two list pages painted once with stale state before correcting themselves.** Integrations
  keeps a map of manual-test results, Services a set of selected rows, and both pruned that
  state in an effect — one render *after* the list it mirrors had arrived. A passive effect
  runs after the paint, so the frame in between is on screen and can be clicked: Integrations
  showed a "last checked" time computed over results belonging to integrations that were no
  longer there, and the Services toolbar read "3 selected" above two rows, with a confirmation
  dialog that would then say two. Neither value is anything but a function of the list and
  what was stored, so both are computed now and the first paint is already right. Restoring
  the manual-test results moved into the `useState` initialiser for the same reason: as a
  mount effect it guaranteed one paint with no results at all, so the badges blinked in a tick
  late on every navigation to the page.
  `eslint-plugin-react-hooks` moves to 7.1.1, the version that reported all of this — 7.0.1
  reported nothing on either file. The two modals the URL contract has to open synchronously
  (`?new=1`, `?edit=<id>`) keep a scoped directive: the modal *is* the page the link asked for.
- **Four operations could fail without a single word on screen.** Signing out was a bare
  `async` function wired straight to `onClick`, so React discarded the promise: a failed
  `POST /auth/logout` became an unhandled rejection, the `auth-status` invalidation never
  ran, and the sidebar still showed a signed-in session. Somebody walking away from a shared
  machine had every reason to believe they had signed out. It is a mutation now — the button
  shows it working, and a failure says so.
  The setup wizard's Finish button called `clearWizardSession()` *before* `onComplete()`, and
  `onComplete()` refetches `/auth/me`, which is a network call like any other. One failure
  and the wizard had already erased every answer just given, while `void finish()` swallowed
  the rejection: no toast, no navigation, a button that looked unclicked. Nothing is discarded
  now until the server confirms. The restore path had the mirror-image defect: four
  `fetchQuery` prefetches — a paint optimisation — were awaited unguarded, so one rejected
  request threw out of the handler and a restore that had *already succeeded on the server*
  never showed its toast and never left the restore screen.
  Finally, the `?edit=<id>` deep links on Services and Integrations only checked `isPending`.
  A failed list load fell through to the lookup, told the operator the item did not exist, and
  `setParam('edit', null)` had already destroyed the deep link — so Retry could not reopen it
  either.
- **The interface stopped reporting a healthy state it had not verified.** Two places, one
  defect: the server did not answer and the UI rendered the reassuring answer.
  `AuthGate` never read `isError`. With `retry: false` a single failed `/auth/me` ends the
  query — `isLoading` drops, `auth` stays `undefined`, and both guards below it are written
  with `auth?.`, so both are falsy and `<AppRoutes />` renders. On a password-protected
  instance a backend hiccup showed the entire app shell to somebody who never signed in
  (the API still refused every request behind it, but the screen said otherwise); on a fresh
  install it walked straight past the setup wizard onto an empty dashboard, which reads as
  "setup is broken". It did not recover on its own either: `refetchOnWindowFocus` is off,
  `staleTime` is 60 s, and the `vauxtra:auth-expired` interceptor deliberately ignores
  `/auth/` URLs. There is now an error screen with a retry.
  Certificate expiry was worse, because the failure was rewritten as data. The dashboard and
  sidebar queries caught the error and *resolved* with a fabricated `expiring_soon_count: 0`,
  so the query never reported an error at all: the tile read "0 of 0", the warning badge
  disappeared, and an operator whose certificate expires in three days was actively told
  everything was fine. All three consumers share the `['certificates-expiry']` key — sidebar,
  dashboard, Certificates page — so that invented zero landed in the cache the other two
  read, and the Certificates page, which never had a `.catch`, never showed its own error
  state because the query had already "succeeded". Both `.catch` blocks are gone: the tile
  shows `—` and says the check is unavailable, "Needs attention" gains an entry saying so,
  and the sidebar badge stays off because the count is unknown rather than zero.
- **PowerDNS no longer deletes the records it could not read.** PowerDNS writes a record
  *set*: `add_rewrite()` reads the existing values, appends one, and sends the whole set
  back with `changetype: REPLACE`. `_zone_rrsets()` returned an empty list for *every*
  failure — a 403 from a zone-scoped API key, a 500, a connection reset, a proxy answering
  HTML — so a refused read was indistinguishable from an empty zone, and the write that
  followed replaced the real record set with the single new value. Every sibling address
  of that name was destroyed: the second A record of a round-robin, the AAAA nobody
  remembered. The call returned `True`. `_zone_rrsets()` now answers `None` when the API
  refused, `_find_rrset()` answers `False`, and both `add_rewrite()` and `delete_rewrite()`
  refuse rather than write blind — the guard deSEC has carried since it was added.
  `list_rewrites()` is unchanged in effect: it is read-only, so a zone it cannot open just
  contributes nothing.
- **`docs/TROUBLESHOOTING.md` gave the one password recovery that makes things worse.** It
  told a locked-out operator to delete `app_password_hash`, which is precisely what
  `docs/HOWTO.md` says is **not** enough: `auth_mode` is the row recording that the
  instance was protected, so with the hash gone and the marker still there Vauxtra refuses
  every request rather than falling back to anonymous access. Following the page written
  for people who are locked out left them locked out harder. Both rows are named now, with
  the reason and a link to the full procedure.
- **Release notes advertised an image tag that is never published.** The body said `docker
  pull ghcr.io/…:${{ github.ref_name }}` — `v1.3.0` — while `type=semver,pattern={{version}}`
  publishes `1.3.0`, without the `v`. Every release since the first handed users a command
  that fails. The publish job now exports the tag it actually pushed and the release body
  quotes that.
- **The weekly GHCR cleanup was making released images unpullable.** A `docker buildx` push
  writes one tagged manifest list and several untagged manifests under it — one per
  platform, plus provenance and SBOM attestations — and those children are what a tag
  resolves to. `actions/delete-package-versions` cannot see that structure: with
  `min-versions-to-keep: 10` it kept the ten most recent untagged versions, which is about
  three builds' worth, and deleted the per-platform manifests of everything older while
  their tags survived. **`1.0.1` and `1.0.2` are already in that state on GHCR** — listed on
  the package page, named in their release notes, cosign signature still verifying, and
  every child manifest returning 404. Replaced with `dataaxiom/ghcr-cleanup-action`, which
  resolves the manifest lists before deleting anything and, with `validate: true`, fails
  the run if a surviving tag no longer resolves. Re-publishing `v1.0.1` and `v1.0.2` is
  what repairs the two already broken.
- **The recommended deployment no longer logs an error at every boot.** Vauxtra serves its
  own interface, so the configuration the documentation recommends allows no cross-origin
  caller at all — and that is exactly the case `validate_cors_origins()` treated as a
  failure. It raised `No valid CORS origins provided` on an empty list, `app/main.py`
  caught it and wrote `Invalid CORS configuration` at `error`, and every correctly
  configured install started with an alarm nobody could clear by fixing anything. An
  absent setting now returns an empty list and logs `No CORS origin configured:
  same-origin callers only` at `info`. A setting that is present but names no origin —
  `CORS_ORIGINS=","` — still raises: something was asked for and nothing took effect, and
  that is worth saying. A malformed origin still raises too, and still refuses the whole
  list rather than widening back to the defaults.

### Changed
- **Example addresses in the interface no longer show a real network.** The local DNS and
  reverse-proxy target placeholders shipped in all eight locales were copied from a real
  LAN. They are now `192.168.1.10` and `192.168.1.20`.

---

## [1.3.0] — 2026-09-10

Two DNS providers, chosen to answer the gap the v1.2.0 provider audit named: three
capability flags, one implementation each, all of them Cloudflare. deSEC is the second
`public_dns`; PowerDNS is the second authoritative server, and the first provider the
integration lab can drive whose write path is a record *set*. 858 tests, up from 738.

### Added
- **deSEC provider (`desec`) — public DNS without a Cloudflare account.** Free, nonprofit,
  DNSSEC by default; a token in an `Authorization: Token` header, one RRset per
  `(subname, type)` under `/api/v1/domains/{name}/rrsets/`. It declares `public_dns` and
  `supports_auto_public_target`, so the external-DNS branch of the expose form and the
  "resolve my public IP" radio now work for an operator who never signs up to Cloudflare.
  The URL is optional (blank means `https://desec.io/api/v1`, for the hosted service) and
  so is the domain: leave it empty and Vauxtra picks the longest domain in the account that
  covers the hostname, or set it to pin every record to one domain.
- **PowerDNS Authoritative provider (`powerdns`).** API key in `X-API-Key`, the server id
  in the username column — `localhost` on every stock install — and records through a
  single `PATCH` on the zone. Zones are matched longest-first, so `app.lab.example.com`
  goes to `lab.example.com` rather than to `example.com` when both are hosted.
- **PowerDNS in the integration lab.** `vxlab-powerdns` on `127.0.0.1:3084`, seeded with
  the `vxlab.test` zone by `bootstrap.sh`, driven through the same lifecycle as the rest:
  register, test, direct record CRUD, publish a service, break the provider behind
  Vauxtra's back, detect the drift, reconcile, delete. The bench is at 94 probes, up from
  74. deSEC is not there and cannot be: like Cloudflare, testing it means a real account
  and a real zone.

### Changed
- **The dependency lot of 2026-09-10.** `requests` >= 2.34.2, `python-multipart` >= 0.0.32,
  `docker` >= 7.2.0, `@types/node` ^26.5.0, and ten SHA-pinned GitHub Actions each a major
  version on: `checkout` v7, `setup-python` v7, `setup-node` v7, `docker/login` v4,
  `docker/metadata` v6, `docker/build-push` v7, `cosign-installer` v4,
  `attest-build-provenance` v4, `gh-release` v3, `sbom-action` 0.24.2. Taken as one lot
  because `main` requires an up-to-date branch -- five separate merges would have meant
  five rebases -- and because the three Python bumps all edit `requirements.txt`.

### Fixed
- **The SBOM step no longer kills a tag publish.** `anchore/sbom-action` defaults
  `upload-release-assets` to true, so on a tag ref it tried to attach the SBOM to a GitHub
  release the pipeline had not created yet: the scan is the gate that runs before the
  build, and the build runs before the release. Permissions were never the problem, and no
  pull-request CI could have caught it -- `security.yml` runs on branches, on pull requests
  and on a weekly cron, but never on a tag.

### Notes
- **Both APIs model a record *set*, and neither add nor delete may write blind.** PowerDNS
  `REPLACE` drops every existing value for a `(name, type)` pair before writing the ones it
  is given, and a deSEC `records: [...]` is the whole RRset. A name with two A records
  would lose one on the next write. Both providers read the current set, merge, and write
  back; both drop the set only once it is empty.
- **A refused read is not an empty read.** deSEC rate-limits hard, and a 429 answered as
  "no records" would have made the merge above delete the very records it exists to
  protect. The listing helper reports refusal separately from emptiness, and both write
  paths give up rather than guess.
- **PowerDNS and Technitium both ship as local DNS on purpose.** An authoritative server is
  public only if its zone is delegated to it at a registrar, and no API says whether that
  happened. Declaring them public would relabel the address field "public WAN IP" for every
  operator running one on a LAN. `docs/PROVIDERS.md` records the reasoning and why the
  row-level override that would express it stays unbuilt.

---

## [1.2.0] — 2026-09-10

Security audit of v1.1.0 and the fixes it produced, the interface pass that followed,
and an integration lab that drives a real Vauxtra against the real provider containers
— which promptly found a Pi-hole session leak no mock could have had an opinion about.
The test suite went from 284 tests to 738.

### Security
- **Path traversal in the SPA catch-all route.** `/{full_path:path}` does not strip `..`
  segments and uvicorn does not normalize the path, so the raw URL segment was joined to
  the build directory unchecked: `GET /%2e%2e/%2e%2e/data/vauxtra.db` served the SQLite
  database, and `data/.secret_key` after it — decryptable provider passwords and a
  forgeable admin session. Paths are now confined with `realpath`.
- **An abandoned setup wizard left ten routes anonymous for good.** `is_setup_incomplete()`
  read only the `setup_completed` flag, written at the wizard's very last click;
  `POST /api/restore` was among the routes it left open. The condition now turns False as
  soon as a password or a provider exists, and the flag is set when either is created.
- **`POST /api/restore` destroyed the instance before validating the passphrase.**
  `executescript()` issues an implicit COMMIT, so the `BEGIN EXCLUSIVE` closed over an
  empty transaction, the thirteen DELETEs committed in autocommit, and the rollbacks
  undid nothing. A dry decryption pass now runs before anything is destroyed.
- **The admin password hash was treated as business data.** The `settings` table was
  readable with a `read` scope, exported in backups, wiped by `/api/reset` and
  `/api/restore` (which dropped the instance back to anonymous admin), and re-injectable
  from an imported file. Read allow-list, protected keys on wipe, import filter, and the
  cleartext export moved to the `admin` scope.
- **Ten routes accepted any authenticated key, scope or not.** `require_auth(request)`
  without `scope=` never consults the scope hierarchy. The preflight probe was the worst
  of them: host and port come from the request body, so a read-only key was a port scanner
  aimed at whatever the container can reach. Also check-all, provider test / validate /
  validate-draft, test-webhook (now `write`), change-password and setup-complete (now
  `admin`), push/dry-run and services/sync (now explicitly `read`). A static test rejects
  any POST/PUT/DELETE/PATCH route that calls `require_auth*` without a scope.
- **The MCP bridge's `--http` mode listened on 0.0.0.0 with no authentication of its own**,
  while holding an API key that reaches every route. It now binds `127.0.0.1` by default;
  `VAUXTRA_MCP_HOST` publishes it, with a warning on stderr.
- **The setup wizard mirrored the provider password into `sessionStorage`** so the form
  would survive a reload — including the proxy admin password or Cloudflare API token.
  Everything else in the form still comes back; that field is typed again.
- **Changing the admin password logged everyone out and changed nothing.** With
  `APP_PASSWORD` set, `check_password` returns on the variable and never reads the stored
  hash — but `POST /api/auth/change-password` wrote one anyway and then bumped the session
  epoch. Every session died, the operator's included, the new password was refused, and the
  way back in was the password they had just tried to retire. The route answers 409 before
  verifying or writing anything, `GET /api/auth/me` carries `password_source`, and the
  Security screen explains instead of offering a form that cannot work.

### Fixed
- **Pi-hole stopped answering after sixteen operations.** Pi-hole v6 allows
  `webserver.api.max_sessions` concurrent API sessions — 16 by default — and holds each
  for `webserver.session.timeout`, 1800 seconds. Vauxtra builds a fresh provider per
  request, so every operation logs in again and takes a seat; only `test_connection` gave
  one back. `list_rewrites` did not, and that is the call the drift check makes on every
  pass. Sixteen of them and Pi-hole refused every login for the next half hour — the
  operator's own browser included, since it draws on the same pool — while Vauxtra
  reported "provider rejected" and pointed at the wrong thing. Restarting Pi-hole does not
  clear it: the sessions are persisted. Every operation now runs inside a session helper
  that releases the seat on the way out, `update_rewrite` spending a single one for its
  add and its delete.
- **Four Settings components were never committed.** The `.gitignore` rule `data/` was
  unanchored, so it matched `frontend/src/components/features/settings/data/` as well as
  the SQLite directory it was written for, and `git add -A` skipped them without a word.
  The build passed on any machine that had them on disk. The rules are anchored to the
  repository root, and the hygiene gate now refuses any source file `.gitignore` hides.
- **CI could not collect the test suite.** `tests/test_mcp_*.py` import fastmcp, which
  lives only in `vauxtra_mcp/requirements.txt`; the workflow installed
  `requirements.txt pytest ruff`. It installs `requirements-dev.txt`, which pulls both.
- **Deleting a service left its routes serving.** `delete_service` walked only
  `proxy_provider_id` and `dns_provider_id`; the extra targets in `service_push_targets`
  were never told, so the hostname stayed resolvable and the proxy kept forwarding with
  nothing left in Vauxtra to show for it. Withdrawal now covers every provider that may
  hold a route, disabled ones included — turning a provider off in Vauxtra does not take
  its published route off the internet. `bulk_action` had the same gap.
- **Deleting a service purged its neighbours' logs.** v1.1.0 narrowed the log cleanup to
  `LIKE '%service <id>%'`, which still matches "service 12", "service 100" and every id
  that merely starts with this one: deleting service 1 wiped the monitoring history of
  nine other services. Now a bounded GLOB. `bulk_action` never purged logs at all.
- **Disabling a service did not cut its public exposure.** In tunnel mode the publish
  branch of `update_service` never read `body.enabled` and republished the ingress rule it
  was meant to remove; `bulk_action` skipped tunnel-mode rows outright. The operator had
  only stopped monitoring the host, not closed it. Editing an already-disabled service
  replayed `add_rewrite`, and a service created disabled was published immediately.
- **A failed Cloudflare Tunnel read looked exactly like an empty tunnel.**
  `_get_configuration()` turned any error into `{}`, and the PUT replaces the whole
  ingress: adding a service during a transient 502 silently deleted every other route in
  the tunnel. It now returns `None` on failure and the writers refuse to act on it.
  `_delete_dns_record` no longer claims success when the zone could not be resolved.
- **No read timeout on any provider call.** `session.timeout` does not exist in `requests`
  — the attribute was set and never read. A provider that accepts the connection then goes
  quiet blocked the scheduler's single thread, stopping all monitoring without a message.
- **The health-check cycle deadlocked itself.** `_run_dns_auto_updates()` ran inside the
  write transaction opened by `run_health_checks()` and opened a second connection; SQLite
  allows one writer, so it waited out its busy timeout, raised "database is locked", and
  every status of that round was lost before the commit.
- **A DOWN alert that failed to send was silenced for good.** `_alert_down_sent` was set
  before `notify()` was even called, and its return value was neither checked nor logged.
  All four notification paths now go through `_try_send_apprise()` — which existed but was
  called nowhere — and a failed send releases the lock instead of keeping it.
- **`_alert_down_since` persisted `time.monotonic()` values**, whose origin changes with
  every process: after a restart, every computed duration was nonsense. Retries were
  stamped in ISO while the queue compares with `next_retry_at <= datetime('now')`, a
  string comparison where "T" sorts above " ": a retry due today only came due tomorrow.
- **NPM forced HTTPS on a certificate that did not cover the host.**
  `find_best_certificate` fell back to the first certificate it found, and `create_host`
  sets `ssl_forced` as soon as an id exists — a wall of errors on every visit. Matching
  now follows the TLS rule (a wildcard covers exactly one label).
- **A toggle from the MCP bridge stripped a service's tags and environments.** The GET
  serializes relations as `tags`/`environments` while the PUT expects
  `tag_ids`/`environment_ids`: pydantic ignored the unknown keys, applied empty defaults,
  and `set_tags` starts with a DELETE.
- **The docs told operators to set `APP_PASSWORD` in cleartext; the code refused it in
  silence.** `check_password` only compares cleartext when `ALLOW_PLAINTEXT_APP_PASSWORD`
  is true (default false), and that variable appeared nowhere outside `app/auth.py`. The
  operator got a 401 on the password they had just configured, a closed setup wizard, and
  not one line in the logs.
- **A half-failed delete said nothing in the UI.** The mutation dropped the response, so
  the row vanished from the table while the hostname went on answering from the internet.
  Provider failures are now reported, and `onError` shows the API's own message.
- **Five French strings carried a `?` where the accent belonged** ("?chec de v?rification
  des services"), and `Settings.tsx` asked for `settings.api_keys.create_failed`, which no
  locale defined — `t()` falls back to printing the key, so the error toast read
  `settings.api_keys.create_failed`.
- **The three big modals were plain divs stacked over the page**: no `role="dialog"`, no
  `aria-modal`, no Escape, and Tab walked out of the box into the form underneath.
- **The log filter queried a level the backend never writes.** The chips asked for
  `ok`, which no code path records, and offered no `warn`, which several do — so
  two filters returned an empty table and one class of log was unreachable. Levels
  are now normalised on write and the same set is used on both sides.
- **The DNS suffix field accepted names the server rejects.** A single label passed
  the browser regex and came back as a 400 from the API.
- **Monitoring poisoned the shared endpoints cache.** It wrote its normalised list to
  session storage on every render, including the empty list that exists before the
  query resolves — so a reload could paint an empty Endpoints page from the cache.
- **A certificate expiring near midnight UTC was counted a day off**, in one direction
  west of Greenwich and the other east: the fallback route serves a naive timestamp and
  the browser read it as local time.
- **Deleting an integration forced the delete without asking.** The call sent
  `force=true` up front, so the 409 listing the endpoints that depend on it was never
  shown. The confirmation now names them before anything is removed.
- Writing to an integration left its inspector panel stale — health, proxy hosts and DNS
  records were keyed per id and never invalidated.

### Changed
- `ServiceIn` now rejects unknown keys (`extra="forbid"`). **Breaking** for any client that
  echoed a GET body straight back into a PUT — it gets a 422 instead of a service stripped
  of its relations.
- The MCP bridge's default request timeout is 120 s instead of 30 s (`VAUXTRA_TIMEOUT`);
  30 s cut off a push that walks the providers in series.
- `DELETE /api/services/{id}` returns `{"ok": true, "errors": [...]}` even when a provider
  refused to withdraw the route: the service is gone from Vauxtra either way, and `false`
  would push a client into retrying a delete that can only answer 404.
- Docs follow the code: a Scopes section in HOWTO, `APP_PASSWORD` /
  `ALLOW_PLAINTEXT_APP_PASSWORD` documented in `.env.example` and DEPLOYMENT, and
  `vauxtra_mcp/README.md` no longer advertises an "all" scope that does not exist.
- Errors the API returns for a preflight refusal, a drift issue or a provider
  diagnostic now carry a `detail_key` and its parameters beside the English
  `detail` (60 codes), so the UI shows them in the user's language instead of an
  English sentence. Old clients keep reading `detail`.
- The interface is fully translated: 297 to 1783 keys per language, the same keys
  in the same order in all eight locales, and no English string left in the code.
- Six environment variables the code reads are documented in `.env.example`:
  `VAUXTRA_PROVIDER_PLUGINS` (an extension mechanism that imports the Python modules named
  in it), `VAUXTRA_REWRITE_LOCALHOST` and `VAUXTRA_LOCALHOST_ALIAS` (whether a provider URL
  on `localhost` is rewritten to `host.docker.internal`, and to what), and the bridge's
  `VAUXTRA_MCP_HOST` / `VAUXTRA_MCP_PORT` / `VAUXTRA_TIMEOUT`. A test holds the sync both
  ways: a variable read without documentation fails, and so does a documented one nothing
  reads.
- `DOCKER_HOST` said "override if using a non-standard location". It only seeds the first
  Docker endpoint on an empty database; afterwards the endpoint list owns the value and the
  variable does nothing. The text says so now.
- Grype is pinned to v0.118.0. At v0.96.0 it could no longer read the vulnerability
  database anchore publishes, so the weekly scan was red on `grype db update` for a reason
  unrelated to this repository.
- `.github/dependabot.yml` is back, grouped. It was deleted in the v1.1.0 release commit
  and nothing watched the dependencies for the three months that followed.

### Added
- **Zoraxy provider** (`zoraxy`, Reverse Proxy). Drives host rules through the management
  API on port 8000 (session login + CSRF token; empty credentials for a `-noauth`
  instance): create, update, enable/disable, delete, import, drift detection, HTTP or
  HTTPS upstream, WebSocket toggle, and the certificate whose CN matches the hostname or
  its parent wildcard recorded as the rule's preferred certificate. Host rules only, one
  managed upstream per rule; no per-host "force SSL" since Zoraxy terminates TLS globally.
- `useModalDialog` — Escape to close, a focus trap, and focus restored to whatever opened
  the dialog. Applied to the expose, provider and connection-editor modals.
- Static guards that need neither a browser nor Node: locale key parity, placeholder
  parity, no accent lost to a `?`, every `t('…')` key defined, and every full-screen
  overlay declaring itself a dialog. CI runs `i18n:quality` but never `i18n:check`, so
  key parity was ungated until now.
- **Rebuilt interface.** One design system behind every screen: 26 primitives
  (buttons, cards, modals, drawers, fields, tabs, empty states, skeletons,
  tooltips), light/dark tokens, a single focus ring, and animations that stand
  down under `prefers-reduced-motion`. The shell gained a collapsible sidebar
  whose badges count what needs attention (enabled endpoints, integrations,
  expiring certificates, endpoints in error), a Ctrl/Cmd+K command palette over
  pages, settings tabs, endpoints, integrations and languages, a shortcuts sheet
  (`?`) with `g`-chords, and a mobile header with a navigation drawer. The theme
  is applied before the first paint, so a dark instance no longer flashes white.
- **Templates page** (`/templates`) — the saved service templates now have a screen.
- Settings split into nine tabs across their own files (`Settings.tsx`: 1909 lines
  to 134), and the data tab into four sections (sync, Docker, export, restore).
- **Integration lab** (`lab/`) — the five self-hostable providers as the container images
  an operator actually runs, and a harness that drives a real Vauxtra against them over its
  own HTTP API: register, test the connection, direct record CRUD, publish a service, break
  the provider behind Vauxtra's back, reconcile, delete. Seventy-four probes over
  `npm+adguard`, `zoraxy+technitium` and `npm+pihole`. `lab/up.sh --fresh` rebuilds the
  whole thing from nothing. Cloudflare and Cloudflare Tunnel are absent because they cannot
  be faked: testing them means a real account and a real zone. This is what found the
  Pi-hole seat leak listed above, and `lab/repro_pihole_seats.py` stays as its witness.

---

## [1.1.0] — 2026-06-12

### Added
- **Service Templates** (`GET/POST/PUT/DELETE /api/templates`, `GET /api/templates/{id}/apply`) — pre-configured service blueprints that pre-fill the service form with provider assignments, scheme, port, domain, and tags. Applied via `/api/templates/{id}/apply` or MCP `apply_template`. DB schema v10.
- **Prometheus metrics** (`GET /metrics`) — no-auth endpoint exposing service counts by status, provider counts by type, log counts (24 h), uptime event counts, webhook stats, template count, and schema version in Prometheus text exposition format.
- **Webhook retry with exponential backoff** — failed Apprise deliveries are logged to `webhook_delivery_log` and retried by the scheduler with backoffs of 60 s, 5 min, 30 min, 2 h, 24 h. Delivered/failed entries are purged after 7 days (configurable via `webhook_retry_retention_days`).
- **Certificate expiry alerts** — scheduler now scans certificates on all enabled NPM providers each health-check cycle and logs `warn` (< 30 days) or `error` (< 7 days) alerts. Deduplication prevents spam: re-alerts only after 24 h or when severity level changes.
- **MCP template tools** (`list_templates`, `get_template`, `create_template`, `delete_template`, `apply_template`) — full template CRUD and one-shot service creation from a template via MCP.
- **284 unit tests** — 7 new provider test files (NPM, AdGuard, Pi-hole, Traefik, Cloudflare, Cloudflare Tunnel, Scheduler) + 2 integration test files (Templates API, Metrics endpoint). All 284 pass.

### Changed
- Expose Route UX now derives DNS behavior from provider capabilities (including `public_dns`) instead of hardcoded provider types.
- DNS-only local routes now derive DNS target directly from the internal target host/IP (without a separate DNS target field), reducing manual friction for LAN-only routes.
- Validation copy now distinguishes local DNS target vs external WAN target to avoid operator confusion.
- Service subdomain validation now accepts wildcard `*` for wildcard host routing (e.g. `*.example.com`).
- Route creation now requires at least one provider target (proxy or DNS), preventing no-op "manual" routes.
- NPM status sync moved from `GET /api/services` (called on every page load) to the health-check scheduler cycle — eliminates blocking HTTP calls on every service list fetch.
- Request-scoped cache now uses `contextvars.ContextVar` instead of a module-level global, making it safe under concurrent async requests.
- `.gitattributes` added — repository line endings normalized to LF; eliminates CRLF conversion warnings on Windows checkouts.

### Fixed
- Services / Expose modal no longer crashes (React error #31) when backend returns structured 422 validation errors; error details are now normalized to user-facing strings.
- Wildcard endpoint links (`*.domain`) are rendered as non-clickable labels to avoid invalid `%2A` navigation URLs.
- `POST /api/services/check-all` now skips tunnel-mode services (previously attempted TCP checks against Cloudflare Tunnel endpoints, always producing spurious `error` status).
- `DELETE /api/services/{id}` log cleanup no longer matches logs from unrelated services that share a hostname substring; scope narrowed to service-ID-specific log entries only.
- `POST /api/services/{id}/reconcile` now correctly fixes DNS drift — previously `push_service` compared stored DB value against itself when they were equal, causing `update_rewrite` to short-circuit as a no-op; push now reads the actual live value from the provider before deciding to delete+re-add.
- `POST /api/services` (proxy+DNS mode) no longer leaves an orphaned NPM proxy host when DNS target resolution subsequently fails — the host is now removed from NPM before the `400` is returned.
- NPM `toggle_host` was calling `PATCH /nginx/proxy-hosts/{id}` which does not exist in NPM; fixed to use the correct `POST /nginx/proxy-hosts/{id}/enable` and `.../disable` endpoints. Service enable/disable from the UI now correctly pauses/resumes the host in NPM.
- Dashboard "Recent activity" → "Logs" link was navigating to `/monitoring` (service health view) instead of `/settings?tab=logs` (full action log).
- Service creation form "Base Domain" was a `<select>` locked to pre-configured domains — if none were configured, the form could not be submitted. Changed to a free-text `<input>` with an optional `<datalist>` for saved domains.
- Internal navigation links in the service form (`/providers`, `/settings`) were plain `<a href>` causing full page reloads; replaced with React Router `<Link>`.
- Service enable/disable now removes DNS rewrites from all DNS providers on disable and re-adds them on enable — keeps DNS state in sync with service state instead of leaving stale records.
- Provider suspend is now generalized: providers that support toggle (NPM) have their proxy host suspended/resumed; providers that do not support toggle (Traefik, etc.) have their proxy host deleted from the provider on disable and re-deployed from Vauxtra config on re-enable. The same logic applies to bulk enable/disable.

---

## [1.0.2] — 2026-05-04

### Added
- Webhook alert rules: `alert_on_any_down`, `alert_on_any_up`, `alert_on_integration_down`,
  `alert_on_integration_up` and `min_down_minutes` — a notification endpoint now says
  *which* transitions it wants, and how long a service must stay down before it is worth
  a message
- Guided provider documentation in the setup wizard — each integration explains what it
  needs before the form asks for it, rather than after the connection test fails
- Technitium: zone handling completed alongside the record operations shipped in 1.0.1

### Changed
- Notification settings rebuilt around those rules rather than a single on/off switch
- Provider constants and the provider form share one source of truth for field labels and
  help text, across all eight locales
- `scripts/check_repo_hygiene.py` widened: the gate now reads the contribution and MCP
  documentation too, not only the application sources

### Removed
- MCP tool `get_logs`. The log endpoint it wrapped is paginated and unfiltered by default;
  an assistant asking for "the logs" pulled fifty lines of mostly noise into its context and
  spent the budget it needed for the answer. `clear_logs` stays.

### Fixed
- Public wording hygiene across `README.md`, `CONTRIBUTING.md`, `docs/HOWTO.md` and
  `vauxtra_mcp/`, and the changelog cross-references that had drifted from their sections

---

## [1.0.1] — 2026-05-04

### Added
- Technitium DNS Server provider — session-token auth, zone auto-detection, A record CRUD
- `Makefile` — `dev`, `test`, `lint`, `lint-fix`, `build`, `release` targets
- `CHANGELOG.md` — this file
- `vauxtra_mcp/README.md` — MCP server setup guide for MCP-compatible clients
- `.github/dependabot.yml` — automated weekly dependency PRs (pip + npm + Actions)
- `.github/pull_request_template.md` — PR checklist
- `.github/ISSUE_TEMPLATE/` — bug report and feature request templates
- `.github/CODEOWNERS` — default code ownership for PR reviews
- Provider modal now shows a "Project website" link for each integration (NPM, AdGuard, Pi-hole, etc.)
- Grype + Syft SBOM scan added to security workflow; scans run on every PR to `main`
- cosign keyless image signing on every published Docker image (Sigstore)
- `ruff.toml` — explicit linter configuration

### Changed
- Split `ci.yml` into three focused workflows: `tests.yml`, `docker-publish.yml`, `security.yml`
- `tests.yml` now runs two parallel jobs: Python (`ruff` + `pytest`) and frontend (`tsc` + `npm run build`)
- `TZ` default changed from `Europe/Paris` to `UTC` across all config files and examples
- `APP_VERSION` is now injected at Docker build time via `ARG`/`ENV`, sourced from the git tag
- `app/config.py` reads `APP_VERSION` from environment (falls back to `"dev"` for local runs)
- `trivy-action` pinned to a specific version (was `@master`)
- In-app "How-To & API" settings panel removed; markdown docs are the single source of truth
- Settings and providers navigation streamlined with per-tab system links and keyboard shortcuts (`g` + `d/p/s`)
- Provider cards now expose clearer operational status labels and health score display
- README/deployment/troubleshooting guides rewritten for operator-focused workflows
- New localhost URL rewrite behavior for provider connections in Docker runtime (`VAUXTRA_REWRITE_LOCALHOST`, `VAUXTRA_LOCALHOST_ALIAS`)

### Fixed
- Removed unused imports across `app/api/` (`get_db_ctx`, `JSONResponse`, `time`, `Any`, `DB_PATH`)
- Removed unused local variables `new_fqdn` / `old_fqdn` in `app/api/services.py`
- `tsconfig.json` root: added `ignoreDeprecations: "6.0"` for `baseUrl` deprecation warning in TS 6+
- Backup restore now forces `setup_completed=1` so restored instances skip first-launch wizard when `settings` table is empty
- Docker endpoint validation hardened to reject malformed `docker_host` URLs
- Webhook URL validation now enforced consistently on create/update/test; partial update path fixed for enable/disable toggles
- Pi-hole v6 test flow now releases API sessions after probe to avoid session slot exhaustion
- Traefik provider submit gating fixed so optional password remains optional

### Upgrade Notes
- Pull and recreate containers to receive updates:
  ```bash
  docker compose pull && docker compose up -d
  ```
- If providers use `localhost` URLs from inside Docker, review `VAUXTRA_REWRITE_LOCALHOST` behavior.

---

## [1.0.0] — 2026-05-02

> **Never shipped.** This tag was created retroactively and points at the same commit as
> `v1.0.1`: PR #20 was squashed, so everything listed below reached `main` in one tree and
> went out as 1.0.1 two days later. No `1.0.0` image was ever built or pushed, and no
> release of that number ever existed until this entry was reconciled with the tag.

### Added
- `app/security.py` — CORS origin validation, domain sanitization, password strength enforcement
- `app/cache.py` — request-scoped caching with TTL expiration (eliminates N+1 provider calls)
- `app/errors.py` — unified error handling with standardized error codes across all endpoints
- Core operator documentation set: `docs/HOWTO.md`, `docs/DEPLOYMENT.md`, `docs/TROUBLESHOOTING.md`

### Changed
- CORS validation: strict origin checking, no wildcards, port and scheme enforcement
- Error responses: standardized codes and shapes across all API endpoints
- `app/main.py` — CORS validation middleware and request cache middleware added

### Fixed
- 8 security issues resolved (CORS, domain injection, password policy, session handling)

---

## [0.1.0] — 2026-04-01

### Added
- Multi-provider service management (NPM, Traefik, Cloudflare, Pi-hole, AdGuard Home, Cloudflare Tunnel)
- Docker container discovery with Traefik label parsing and confidence scoring
- Preflight validation, dry-run push, drift detection, and reconcile
- Auto-reconcile scheduler with webhook (Apprise) notifications
- Certificate expiry monitoring
- API key authentication (Bearer tokens) for CI/CD and MCP
- MCP server exposing core operations as tools for MCP-compatible clients
- React 19 + TypeScript SPA with Tailwind CSS
- SQLite (WAL mode) — zero external dependencies
- Multi-architecture Docker image (linux/amd64 + linux/arm64) via GHCR

---

[Unreleased]: https://github.com/ptitzgeg-on-git/vauxtra/compare/v1.4.0...HEAD
[1.4.0]: https://github.com/ptitzgeg-on-git/vauxtra/compare/v1.3.0...v1.4.0
[1.3.0]: https://github.com/ptitzgeg-on-git/vauxtra/compare/v1.2.0...v1.3.0
[1.2.0]: https://github.com/ptitzgeg-on-git/vauxtra/compare/v1.1.0...v1.2.0
[1.1.0]: https://github.com/ptitzgeg-on-git/vauxtra/compare/v1.0.2...v1.1.0
[1.0.2]: https://github.com/ptitzgeg-on-git/vauxtra/compare/v1.0.1...v1.0.2
[1.0.1]: https://github.com/ptitzgeg-on-git/vauxtra/compare/v0.1.0...v1.0.1
[1.0.0]: https://github.com/ptitzgeg-on-git/vauxtra/releases/tag/v1.0.0
[0.1.0]: https://github.com/ptitzgeg-on-git/vauxtra/releases/tag/v0.1.0
