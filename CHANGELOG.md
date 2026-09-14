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

- **`vauxtra_mcp/README.md` now has a "When a call half-succeeds" section**, and a test that
  keeps its table true. The docstrings say what each key means at call time; this is what
  somebody reads before writing the integration, and it carries the rule none of them can
  state alone: the status code and `ok` are both unreliable signals of a partial failure, so
  the list is the result. `POST /api/services` is the only route in the product that marks it
  in the status at all, answering 207, and `check()` passes a 207 through as a normal answer
  because the call did do something. The two deletion routes then disagree about the flag —
  `DELETE /api/services/{id}` answers `ok: true` with records still live, since the service is
  gone from Vauxtra and a false `ok` would only invite a retry that can answer nothing but
  404, while `DELETE /api/providers/{id}` answers `ok: false` in the same situation. A caller
  reading either flag as "it worked" is wrong about one of them.

  The table names all fourteen pairs across eleven tools, one row per key, and separates the
  two that read like failures and are not: a `skipped` line is a name Vauxtra already tracks,
  an `ignored` line is a read-only key handed back untouched, and folding either in with
  `errors` invents work that does not exist while burying work that does.

  A hand-written list of fourteen pairs is correct the day it is written and quietly wrong two
  routes later, so `TheHalfSucceedsTableNamesWhatTheCodeAnswers` parses that table and compares
  it to what the routes actually answer, in both directions — a pair the README omits is a
  partial failure nobody was warned about, a pair it invents sends a reader looking for a key
  that is not there. The parser is scoped to the one section and reads only its first two
  columns, and a heading that gets renamed reads as an empty table, which disagrees with every
  pair the code answers and so fails the build rather than passing it.

- **A capability parity gate**, `scripts/check_capability_parity.py`, run in the backend job
  beside the runtime parity gate. It reads `PROVIDER_TYPES` out of `app/providers/factory.py`
  as a syntax tree, `CAPABILITY_FALLBACK` out of `frontend/src/lib/providers.ts`, and the
  `ProviderCapability` union out of `frontend/src/types/api.ts`, and requires the table to
  give the backend's own answer for every type the backend ships. A type it does not ship is
  left alone, since standing in for those is the whole reason the table exists. Two rules
  come free from reading both files: a fallback naming a type this build cannot create, and
  a capability the backend declares that the union does not name — the second matters because
  the first rule iterates the union, so an unnamed capability would be skipped in silence.

- **A read contract gate**, `scripts/check_read_contract.py`, run in the backend job beside
  the panel contract gate. That one holds what the panel sends; this one holds what it
  believes it gets back, which was held to nothing at either end. Not one route in `app/`
  sets a `response_model=` — every `GET` returns a bare dict or list assembled in Python —
  and `api.get<T>(url)` ends in `return _axios.get<T>(url, config) as unknown as
  Promise<T>`, a cast with no validation behind it. A declared answer shape is therefore a
  claim about bytes that nothing checks: `tsc` believes it by construction, FastAPI never
  sees it, and a wrong one is not a type error anywhere. It is an `undefined` at runtime, in
  a branch, on somebody's dashboard.

  Checking every declaration against the real answer would mean running the backend, which
  this job does not do. One thing is decidable by reading alone: when two places read the
  *same* bytes — the same route, or the same react-query cache key — and each names its own
  type, at most one of them can be right. Sixty-seven `api.get<T>` calls and sixty
  `useQuery<T>` blocks fall into twenty-seven groups with more than one reader, and 230 pairs
  of those readers are compared key by key, in type and in optionality both. Utility types are
  expanded rather than refused, because one of them is load-bearing: `Template` is
  `Omit<TemplateIn, 'websocket'>` with a `websocket` of its own put back. `Pick` and `Partial`
  resolve the same way; no panel module spells one over an answer today.

  Reading *fewer* keys than arrive is not a disagreement and does not fail the build.
  `pages/Setup.tsx` reads `/providers` as `ProviderItem[]` where eleven other sites read
  `Provider[]`; the eight keys it leaves out are counted, not refused, so a narrowing that
  grows stays visible without a red build. Five pairs cannot be compared at all, and each of
  the four groups holding them carries a written reason: the settings table is rows rather
  than fields, so `Record<string, string>` is as true about it as `AppSettings`, and the
  Dashboard reads `/providers/health` as `unknown` on purpose because it only counts the
  entries that came back. A reason for a group that compares cleanly fails the build — the
  same rule the panel gate applies to its own exemptions.

  The second rule has no exemptions at all: no panel module may declare a type name
  `frontend/src/types/api.ts` already declares. Two declarations of one answer is two things
  to keep in step, and the one that drifts is the copy sitting where a reader looks first.
  Five were deleted to reach zero, and every one of them was the stale half: the shadowed
  `DockerContainer`, `AuthStatus`, `Provider`, `ProviderValidationResult` and `GuidedStep`.

  The third rule pairs a `useQuery` with the URL its `queryFn` reads and fails when one URL is
  read under more than one cache key. A key is a lifetime: two keys over one answer means every
  place that invalidates after the answer changed has to name both, which is a thing the
  compiler cannot check and people do not do. The pairing is verbatim rather than by route, so
  `/logs?per_page=8` and `/logs?page=${page}` stay two questions and the rule needs no exemption
  table at all — `/logs` is read under three keys and is right to be. It caught one: `/auth/me`,
  read under `['auth-status']` by the shell and under `['auth-me']` by Settings, fixed below.

  The rule reads `queryOptions` as well as `useQuery`, and resolves a key hoisted into a `const`
  to the array it was declared with, because that is the shape of the fix it asks for: a shared
  hook naming the key once and describing the query in one object. Reading only a literal
  `queryKey` on a `useQuery<T>` would have made the rule blind to `useAuthStatus` and to the
  older `useProviderTypes`, so re-splitting either entry would have gone green. Sixty-two of the
  sixty-four blocks that carry their options inline pair a key with a URL. The two that do not
  are the generic taxonomy tab, whose key and endpoint both arrive as props, and the monitoring
  page's log query, whose `queryFn` is a named function rather than a call.

  The fourth rule reads which of a query's states each caller ever names. `useQuery` reports
  a request that failed and one that answered with nothing as two different states, and a
  caller naming neither receives both as an absent `data` — so what reaches the screen is
  the fallback written beside the read, `?? []`, `?? 0`, `|| ''`. A list nobody could fetch
  is then drawn as a list with nothing in it, which is not a slower answer to the operator's
  question but a different one, given with the same confidence.

  It is the only rule here that caught nothing on the commit that introduced it, which is
  what it is for. The eighteen commits before it each gave one panel read a way to say it
  had failed: a command palette answering "No matches." over a list it never received, two
  wizard steps hiding their own, a certificates page reporting on an integration it had not
  read, a form telling an operator that his proxy cannot detect a public address when the
  lookup had never come back. Every one was a `useQuery` read down to its `data`, and every
  one was found by reading the file. This rule is what stops the nineteenth.

  Of the sixty-nine `useQuery` sites in the panel, fifty-two name a signal, nine hand the
  whole query object somewhere this gate cannot follow — a hook returning it to its callers,
  which is where its state is read — and eight withdraw into something that states nothing.
  Those eight carry a written reason naming what gets drawn instead: a sidebar badge that
  does not appear, a dash where a version would be, the browser's own time zone, a cadence
  reported as `unknown`. A reason that stops being true fails the build, the same way a dead
  entry in the first rule's table does.

  The exemptions are keyed by file and bound name rather than by line, because a line number
  goes stale on the next edit above it and an excuse that moves is an excuse nobody rereads.
  Two reads in one file bound to one name therefore collide rather than share one, which is
  its own failure with its own message. `isPending` and `isFetching` are deliberately not
  accepted: they describe a request still in flight, and a caller naming only those still
  cannot tell the other two states apart once it lands.

  `tests/test_read_contract_parity.py` reads four of those shapes back out by hand, builds
  the disagreements the panel no longer contains so the comparison has something to fail on,
  resolves the fourth rule against ghost sources written for one case each — a read that
  names no signal, one that names any of the five, one handed back whole, one renamed to
  `error` without ever having asked for it — and pins the premise itself: no `response_model=`
  anywhere in `app/`, and a client that casts rather than checks. If either half of that
  stops being true, this gate is the wrong place to catch the problem and should go.

- **A panel contract gate**, `scripts/check_panel_contract.py`, run in the backend job beside
  the API-MCP parity, repo hygiene and runtime parity gates. `check_api_mcp_parity.py` holds
  the MCP bridge to the Pydantic model of the route it posts to; the panel posts to those same
  routes and was held to nothing. Pydantic drops a key it does not declare, so a field the form
  filled in is discarded in silence and the save answers `200`: the column never moves, and the
  next `GET` hands back the old value with nothing to say why. TypeScript does not close this —
  `api.post<T>(url, payload)` types the *answer*, never the body, and the body's own type is
  declared in the panel, so the panel can promise a field the server has never heard of and
  `tsc` agrees, because both halves are internally consistent and neither has read the other.
  Eighteen of the twenty models the panel posts to accept unknown keys; only `ServiceIn` and
  `ServicePreflightIn` refuse, and the other eighteen are what make this worth a build.

  It reads every `api.post` and `api.put` body under `frontend/src` and resolves the key set by
  binding names the way the language binds them: an inline object literal with its spreads
  resolved recursively; an identifier bound by the enclosing function's parameters, annotated
  directly, destructured out of an annotated object, or typed by the third generic argument of
  its `useMutation`; an identifier bound by a `const` in a block that contains the call; or a
  call resolved through its callee's declared return type. Names are looked up the way a module
  looks them up — this file, else the file this file imports the name from, following a
  re-export through to whichever file finally declares it, else a single unambiguous
  declaration in the panel. That last part carries weight: `buildPayload` is declared twice,
  in `ExposeModal.tsx` and in `providerConstants.ts`, with no key in common, and six type
  names are declared in two files each. `StatusFilter` is the sharpest: `monitoring/uptime.ts`
  calls it `MonitoringStatus | 'all'` and `services/helpers.ts` calls it `'ok' | 'error'`,
  and `pages/Monitoring.tsx` and `pages/Services.tsx` each import the one they mean. A flat
  repo-wide table would union the two and answer with a filter neither page accepts.

  A resolver that guesses is worse than one that refuses. An earlier draft took "the last
  `name:` above the call", read `data` off an `onSuccess` twenty lines up, and reported a
  provider update as sending `ok, health, validation`; a wrong key set both misses real
  divergence and invents fake divergence, and the fake one teaches everybody to ignore the
  gate. So thirty of the thirty-six bodies are compared, two go to routes that read a free-form
  dict where there is no field list on either side, and the remaining four are each listed with
  their reason: two whose body is a bag of whatever keys it was handed, a settings form and a
  restored backup file, and two that drive `/api/tags` and `/api/environments` from the same
  config field, where the body is legible and the route is not. A call that stops being listed
  fails the build, the same rule the parity gate applies to its own exemptions: an allowance
  nobody is watching is one the next call site inherits without ever having argued for it. Only
  the direction that loses data is a failure — a field the model declares and the panel omits
  is a default doing its job.
  `tests/test_panel_contract_parity.py` reads eight call sites by hand, one per resolution
  path, and holds the gate to them; it pins the two-declaration cases by name; and it proves
  over real HTTP that an unknown key is dropped with a `200` rather than refused, so the
  premise the gate rests on is measured rather than asserted.

- **A gate for the one locale question neither existing gate could ask: does the code name a
  key that exists?** `check-locale-parity.mjs` and `check-locale-quality.mjs` read
  `src/locales/*.json` and never open a component, so between them they check that the eight
  files agree with each other, and nothing checks them against the code that calls `t()`. The
  cost of that gap falls on an operator rather than on CI: `t()` returns the key it was given
  when it misses, so a single typo ships as `settings.logs.autoscrol` printed in the middle of
  the page, in all eight languages at once, with every check green.

  `frontend/scripts/check-locale-usage.mjs` reads every literal key a `t()` call spells out in
  `src/**/*.ts` and `.tsx` — 1798 of them today — and fails if one is absent from `en.json`,
  accepting a plural base whose `_other` form exists. It runs from `npm run i18n:check`, which
  CI already calls, so no workflow changed. A call Prettier wrapped onto a second line is the
  same call and is checked; a key shown as an example inside a comment is not a call and is
  not, so documenting `t()` in a JSDoc block stays safe.

  Only literal names are checked. The provider wizard assembles
  `provider_guide.${type}.step_N` and the taxonomy tab keeps its prefix in a config object;
  resolving those takes a guess, and a gate that guesses fails on code that is correct. The
  reverse question — which keys nothing reads — is deliberately left ungated: a dead key is
  inert, a wrongly deleted one prints its own name to an operator, and measuring it took four
  calibrations before the count stopped moving.

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

- **An agent could delete a provider only in the way that leaves its records published, and
  nine tools reported partial failures as success.** Two holes in the same surface, both of
  them in what the MCP bridge lets an agent do and say.

  `DELETE /api/providers/{pid}` takes `withdraw`, which decides whether the provider's proxy
  hosts and DNS records are taken down before it is forgotten or left live on a provider
  Vauxtra no longer knows about. The panel has offered that choice as a checkbox since it was
  written and the API tests cover both branches; the bridge declared no such parameter, so the
  only provider deletion an agent could perform was the one that orphans published records —
  a hostname still resolving and still proxying, with nothing in Vauxtra pointing at it.
  `check_api_mcp_parity.py` exists to catch exactly this and could not: it compared query
  parameters in one direction only, reporting a key a tool *sends* that its route does not
  read, and never the reverse. A parameter no tool sends is invisible to a check that only
  reads what tools send.

  The second hole is what the tools say. FastMCP publishes the signature and the docstring and
  nothing else, so for an agent the docstring is the entire description of the answer. Several
  routes here answer a partial success: `delete_service` returns `{"ok": true, "errors":
  [...]}` where `ok` means the service is gone from Vauxtra and `errors` holds the records
  still live on their providers; `save_settings` returns `not_applied` for a setting written
  to the database but never handed to the running scheduler; `import_docker_containers`
  returns `skipped` and `errors` for two opposite outcomes. Nine tools returned one of those
  keys without naming it, ten pairs of tool and key in all, while four such pairs across three
  tools spelt theirs out — drift from a house rule, not a missing convention. An agent reading
  `ok: true` reported the work done, and the list beside it holding what had not happened went
  unread. `not_applied` is the clearest case: the route builds it, the tests pin it, `api.ts`
  declares it, `GeneralTab.tsx` renders it and all eight locales translate "Saved, but not
  running yet". The bridge alone was silent, on the one surface with no human reading the
  screen.

  Two of the nine surfaced only once the new pass learnt to read a dict through the
  `JSONResponse(...)` wrapper a route uses to answer 207 rather than 200. `POST /api/services`
  is written that way: it publishes the tunnel route, the proxy host and the DNS record,
  collects every refusal into `errors`, stores the row regardless and answers 207 when the
  list is not empty. Both tools that call it — `create_service`, the most-used write tool in
  the bridge, and `apply_template` — described that answer as "the created service record".
  An agent creating a service whose proxy host was refused got back an id, an fqdn, and a
  reason it had been given no word for, and reported a service created and reachable when
  nothing routed to its hostname.

  `withdraw` is now a parameter, and the nine tools name their keys and say what to do about
  them. The gate gained the two missing directions in the shape the others already have — a
  table whose entries each carry a written reason, and a stale entry that fails the build —
  so `UNREACHABLE_QUERY_COUNT` and `SILENT_PARTIAL_COUNT` are now printed alongside the rest
  and both read 0. Fourteen tests pin them, including witnesses on a throwaway repository that
  prove each collector can tell a hole from a fix rather than merely reporting nothing, and one
  that fails if the answer reader stops seeing through the wrapper.

- **The dashboard counted a service nobody was watching as a fault, and the same card said
  two different things about the same estate.** `GET /api/stats` answered `services_ok` and
  `services_error` over every row in `services`, enabled or not. Nothing else in the product
  reads health that way: `serviceStatus()` opens with `if (!service.enabled) return
  'disabled'` — a state of its own, in neither bucket — and the dashboard's own fallback
  counts `enabledServices.filter(...)`. The tile is written `stats?.services_ok ??
  servicesOk`, so the route's figure wins whenever it answers and the list's figure appears
  only when it does not, and the operator read one number or the other depending on which.
  Four disabled services and one live one made a card that read "2 enabled, 2 ok, 2 error".

  The column made it permanent rather than momentary. The scheduler probes `WHERE enabled=1`,
  and the only writer of the flag is `UPDATE services SET enabled=?`, which never touches
  `status`. A service disabled while failing therefore keeps `status` `'error'` for as long as
  it exists: the tile stayed red, with a count, and the triage list directly beneath it —
  read off the enabled-only list — had nothing to show. Clicking the count opened
  `/monitoring?status=ok`, which filters on `serviceStatus`, so the list that opened
  disagreed with the number that opened it.

  Both counters are now scoped to enabled services, which is the definition the rest of the
  product already used. Eight tests pin it: the 2x2 of enabled against status measured
  through the route, an invariant that health can never outrun what is watched, and text
  pins on the four other files that hold a piece of the rule, so a second definition of it
  turns red here.

- **"This proxy cannot detect its public target" was printed over a lookup that never
  answered.** `GET /api/services/public-target/suggest` reaches `detect_server_public_ip`,
  which makes a real outbound call, so the read is both slow and failable — and it was
  destructured down to its data, its fetching flag and its refetch, with no failure state.
  Its answer carries an empty `recommended` when nothing replies, so a lookup that failed
  and a proxy that genuinely has no public address arrived in the same shape.

  Two things followed. The Detect button spun, stopped, and changed nothing on screen, with
  no way to tell a timeout from a proxy that had nothing to report. And `validate()` refused
  to continue with a sentence naming the proxy as the thing that cannot do it — a verdict
  passed on a subject the app had learnt nothing about, sending the operator to check
  provider credentials over a request that had merely timed out.

  The lookup now says which of its three outcomes happened. A failure raises a warning
  beside the field, and stays up even once a target is typed by hand, because the automatic
  DNS update switch below reads the same lookup. A lookup that answered with nothing says
  so plainly, and goes away as soon as a target is typed, which settles that question. And
  the refusal to continue splits three ways: the lookup could not be made, the lookup is
  still running, or — only now with an answer behind it — this proxy cannot detect its
  public target. Nine tests cover the modal, which had none; six fail on the old code.

- **The command palette answered "No matches." for an endpoint that exists.** `['services']`
  and `['providers']` are the only place the box learns what the estate holds, and both were
  destructured down to their data with no failure state — the same shape `useDockerEndpoints`
  carried until the wizard's three screens paid for it. A read that failed and an instance
  with nothing registered arrived as the same `undefined`: the two groups went missing from
  the list, and the sentence underneath stated flatly that nothing matched. That sentence is
  a claim about the estate, and it was being made from a list nobody had read.

  The in-flight case reads the same and is the likelier one. The palette answers Ctrl/⌘ K
  over any page, mounts fresh each time, and its two reads carry a thirty-second staleness
  window; open it from Settings, Monitoring or Certificates — none of which read either list
  — and the first keystrokes land while both requests are still open. An operator who types
  a hostname, reads "No matches." and concludes the endpoint was deleted is reading a report
  on a question that had not been answered yet.

  The palette now states the failure in a banner above the results, with a retry that asks
  again only for the list that failed, and keeps that banner up while the pages and commands
  it can still search go on matching — a partial answer is the case where the missing groups
  are hardest to notice. "No matches." is now printed only once both lists have actually
  answered; until then the box says it is still reading, and says so in the footer too when
  results are already on screen. Six tests cover the palette, which had none.

- **Two of the wizard's optional steps hid the list they were built around, and the screen
  that congratulates the operator counted from both.** `useDockerEndpoints` handed its rows
  out with no failure state at all, the way `useWebhookActions` used to before the comment
  beside its own query spelt out why it must not: an empty array after a failed fetch and an
  empty array on an instance with nothing registered are the same array. Three screens paid
  for it. The Docker step and the notifications step drew a read that had failed as nothing
  at all — no list, no message, and a footer button offering to skip a step whose contents
  nobody had seen. The shell binds Enter to that button, so the fastest way through the
  wizard confirmed the claim.

  On the notifications step the cost is not cosmetic. `POST /api/webhooks` carries no
  duplicate guard — no UNIQUE index, no lookup — unlike `POST /api/docker/endpoints`, which
  answers 409. An operator who re-adds the target they cannot see ends up with two rows, and
  every alert from then on fires twice on the same channel, for good.

  The celebration screen printed `formatNumber(0)` beside "Notification targets" and "Docker
  hosts" whenever those reads had not answered, and usually they have not: it mounts the
  instant the wizard finishes, with both requests still open. `StatRow` settled the rule for
  this repo — zero and "we could not ask" look identical as a number, and only one of them
  means everything is fine — and six dashboard tiles already followed it.

  Both steps now hold the place of the list while it is in flight, state the failure with a
  retry when it fails, and keep the neutral "Continue" label until the list has actually been
  seen; a failed read still lets them move on, since neither step is required. The summary
  prints a dash rather than a nought for a figure nobody could ask for, and says so once
  underneath, with a retry that refetches only the read that failed. Eighteen tests across
  three new files cover the three screens.

- **Both dialogs that create a route blamed the operator's setup for an integration list
  they had never read.** The exposure wizard destructured `/providers` as `data = []` with
  only an `isLoading` beside it, while the three reads under it — domains, tags,
  environments — each carried an `isError` the form already knew how to say. A request that
  failed is not loading, so the skeleton gave way to a form drawn from an empty list, and
  that form spoke: no reverse proxy is configured yet, no other proxy provider available, no
  DNS provider. `validate()` then refused to continue, correctly, and named the reason as
  the operator's own setup — "choose at least a proxy or a DNS provider" — for a list
  nobody had read. The only way out it implied was the Providers page, to create
  integrations that were already there.

  The template form did the same with two reads instead of one, `/providers` and `/domains`,
  and said it more plainly: an integration field whose list is empty replaces its select
  with a notice stating that no reverse proxy, or no DNS provider, or no tunnel provider is
  configured yet, and offers a link to go and create one. That notice is a claim about the
  instance; it was being made from an unchecked read, and the link closes the half-filled
  form on the way out.

  Both lists are now read whole. Each dialog states the failure once, with a retry that
  refetches only the read that failed and reports itself busy for that read alone, because
  the busy state also disables the button it is on. In the wizard the two "no other provider
  available" lines say the list could not be loaded instead, and only while the list is
  genuinely unread: a refresh that fails over rows that already arrived leaves those rows in
  the selects, and a list that is empty at that point is empty for a real reason. In the
  template form the invitation to go and create an integration is withdrawn until a list has
  come back — pending counts as unread, since nothing has answered yet — and each
  integration field says instead that the choice offered is incomplete. A list that does
  come back empty still says the instance has none, and still invites.

  `ServiceForm.test.tsx` gains the wizard's provider read: the warning, the two withdrawn
  lines, the retry and its busy state, and the two controls that keep the honest empty state
  honest — an instance that really has no provider, and a refresh that failed over rows
  already in hand. `TemplateModal.test.tsx` is new and covers the template form's two reads:
  both integrations and the domain named when they answer, the warning for each read and the
  single warning when both fail, the hint on both integration fields while the list is
  unread or still in flight, the retry touching only what failed, and the control that a
  list which did come back empty still invites.

  Two suite-wide budgets are stated rather than defaulted while that new file goes in.
  `asyncUtilTimeout` governs every `findBy*` and every bare `waitFor` here and defaults to
  1000 ms, a figure meant for a DOM assertion; these components mount a query client and
  wait on reads, and the first test in a file also pays the run's one-off warm-up. The
  heaviest of them — the template form's happy path, which waits for both reads to land —
  measures 573 ms in first position and 241 ms anywhere else on an idle eight-core machine,
  against 60 to 170 ms for every other test in its file: 1.7 times its own cost in headroom
  where everything around it had ten. It went red under the full suite on a busy machine,
  and the same-shaped first test of `Templates.test.tsx` went with it; CI runs on a runner
  with half the cores those figures came from. Under the load that produced both failures
  the suite now passes whole, the two tests taking 3.3 s and 4.8 s. A budget only elapses on
  a test that is failing anyway, so the longer one costs nothing on green.

- **The Docker import wrote one route per container with no integration attached, from a
  list nobody had read, while telling the operator the instance had no domain.** The panel
  is configured from two reads it never checked: `/providers`, which fills both the proxy
  and the DNS select, and `/domains`, which fills the domain select. Both were taken as
  `data ?? []`, so a request that failed and a request still in flight both arrived as an
  empty list. An empty provider list narrows both selects to a single "None" option, which
  reads as a choice rather than as the absence of one, and the import then goes through and
  writes one route per selected container with nothing attached — a decision the operator
  never made, on as many rows as they had selected. An empty domain list said two things at
  once, neither of them measured: the select states "No domain configured", which is a claim
  about the instance, and the same empty list empties `effectiveDomain`, which is what the
  import button reads to disable itself. The button went dead with no reason given.

  Both lists are now read whole. The panel states the failure once, with the reason the read
  gave and a retry that refetches only the read that failed; that retry reports itself busy
  for that read alone, because the busy state also disables the button it is on. The domain
  select offers "Domain list unread" instead of "No domain configured" until a list has come
  back, and a list that does come back empty still says the instance has no domain. Both
  integration selects carry a hint saying the choice offered is incomplete while the list is
  unread. And because the import is the point of no return, the confirmation in front of it
  says in as many words that nothing will be attached, and opens on Cancel, whenever the
  provider list is unread and neither select holds a value.

  `DockerSection.test.tsx` gains the panel's two configuration reads: the warning for each of
  them, the single warning when both fail, the domain offered as unread rather than as absent
  with the control that a list which did come back empty still says so, the hint on both
  integration selects, the retry touching only what failed and staying usable while a sibling
  read is still in flight, and both shapes of the confirmation in front of the write.

- **Every card on the templates page said "Provider removed", in warning colour, whenever
  `/providers` had not answered.** A template stores ids, and nothing on that page is a
  template on its own: the integration line under every card, the label chips on it and the
  whole filter row above them are `/providers`, `/tags` and `/environments` resolved against
  ids the template holds. All three were read as `const { data = [] } = useQuery(...)`, so a
  request that failed and a request still in flight both arrived as an empty map, and every
  lookup in an empty map is a miss. The card had one sentence written for a miss — the
  integration was deleted — and printed it about integrations nobody had touched, which
  invites the operator to go and re-create what is still there. The two label reads were
  quieter and no better: the chips came off cards that carry labels and the filter row
  vanished outright, with nothing said in place of either.

  The card is now told whether the map it was handed is an answer at all, and says
  "Provider list unread", in the muted colour it uses for what it does not know, instead of
  accusing a live integration. A miss in a catalogue that did come back still says
  "Provider removed" in warning colour, because there it is a measurement. The page states
  the failure once, with the reason the read gave and a retry that refetches only the reads
  that failed; the filter row comes back with it. That retry reports itself busy for those
  reads alone, because the busy state also disables the button it is on, and a sibling read
  that never answers would otherwise hold it shut for good. The id-based filtering never
  depended on these maps and is unchanged.

  `frontend/src/pages/Templates.test.tsx` is new and covers the page: the warning for each
  of the three reads, the single warning when all three fail, the filter row leaving and
  returning, the retry touching only what failed, and the retry staying usable while a
  sibling read is still in flight. `TemplateCard.test.tsx` covers the card, including the
  control that a genuinely deleted integration is still named as one.

- **The count beside a domain and the question deleting it asks were both read out of a
  query nobody checked, so a read that failed looked exactly like an empty answer.** `DnsTab`
  and the two editors of `TaxonomyTab` build the map of what a row is holding from
  `/services` and `/templates`, and took both as `const { data = [] } = useQuery(...)`:
  neither `isError` nor `isPending` came out, so a request that failed, and a request that
  had simply not landed yet, were an empty list. On the DNS tab that was stated rather than
  left blank — every row painted a literal "0 services" over a read that had answered
  nothing — and on both tabs the deletion then asked the question written for
  something nothing is built on, the one whose whole content is that nothing else changes.

  Nothing further down refuses it. `DELETE /api/domains/{name}` looks the holders up only to
  journal them, and `DELETE /api/tags/{id}` takes `service_tags` with it by cascade. The
  dialog is the entire guard, and what decided which one to ask was the emptiness of a read
  nobody had checked.

  Both tabs now separate "nothing holds this" from "nobody knows". A third branch, first in
  the chain, asks a question that says the services and the templates could not be read and
  that the count is therefore unknown; the deletion stays available, because a tab must not
  be stranded on a read it may never get. The DNS rows swap the false zero for a "usage
  unknown" badge, and each tab carries one warning with a retry that refetches both reads at
  once. The taxonomy chips keep showing no number, deliberately: an absent count there is
  not a statement, where "0 services" on a domain row is.

  Seven keys in the eight locales. Sixteen tests across `DnsTab.test.tsx` and
  `TaxonomyTab.test.tsx`, whose mock now answers a read with rows, with a failure, or never.
  Witness: with the previous two components in place fourteen fail and two pass, the two
  being the positive controls — a deletion that still goes through once the unknown question
  is confirmed, and the plain question still asked over a label two answered reads agree
  nothing carries.

- **The bridge could build a taxonomy and had nowhere to put it: every service an agent
  created was unlabelled, for good.** `create_service` sent `tag_ids: []` and
  `environment_ids: []` in its body and declared no parameter for either, so a call naming a
  tag was refused by the tool's own schema before the body ran — `2 validation errors for
  call[create_service]`, `unexpected keyword argument`. `update_service` declared neither
  either, so the service could not be labelled afterwards. Meanwhile the bridge publishes
  eight tools for building tags and environments: create, update, delete and list, twice
  over, with the fourteen colours validated on the way in. The only labelled service it
  could produce came from `apply_template`, wearing whatever the template carried at the
  moment it was applied, with no way to change it later.

  Both tools now take `tag_ids` and `environment_ids`, the shape `create_template` and
  `update_template` already had. On the update the two follow the overlay rule the tool
  documents, with the edge it implies written out: omitted, the labels survive, because the
  payload is built from a GET; named, they are the whole new set, because
  `PUT /api/services` replaces rather than merges and `set_tags` opens with a DELETE. So
  `tag_ids=[3]` on a service carrying 1 and 2 leaves it carrying 3 alone, and `tag_ids=[]`
  strips every label. An id naming no row is refused with 400 that names it, so a typo
  creates nothing rather than a service quietly missing a label.

  `scripts/check_api_mcp_parity.py` compares tool signatures with the models their routes
  enforce and could not see this, because it asks whether a tool declares what a route
  *requires* and `ServiceIn` gives both fields a default. Nothing was required, so nothing
  was reported, for a field no caller could reach. A fourth pass now reads the body every
  write tool sends and reports each key spelt as a bare literal: that key is not a default
  a caller may override, it is the only value that route will ever see from that tool.
  Eleven are deliberate and carry their reason in `ALLOWED_UNREACHABLE_FIELDS` — seven on
  `run_preflight`, whose route stores nothing and whose checks read fourteen fields of the
  body and none of those; the multi-sync push targets, which the bridge offers nowhere, on
  the same grounds as the per-provider record routes it already declines to expose; the
  icon URL, which no agent has anything to derive; and the `enabled: True` of
  `apply_template`, a template being applied in order to publish. An entry that stops
  matching fails the build, as the three exemption tables beside it already do.

  Fourteen tests, all of them in `tests/test_mcp_contract_parity.py` alongside the rest of
  the bridge's contract coverage. Witness: with the previous `services.py` in place six
  fail and eight still pass, the eight being the positive controls — the default that
  attaches no label, the labels the overlay preserves when nothing is passed, and the four
  synthetic tools that show the new pass reports a hardcoded key, stays quiet for one a
  parameter feeds, ignores a dict that is not a request body, and ignores a read.

- **A capability table that stood in for the backend had drifted from it in five places,
  and one of them silently revoked an operator's setting.** Every screen that asks whether
  an integration can do something goes through `metaHasCapability`, which reads the
  `capabilities` map from `GET /api/providers/types` first and a hardcoded table,
  `CAPABILITY_FALLBACK`, last. That table is the only answer before the query resolves and
  for as long as it fails, and it cannot say "unknown": a capability it does not name reads
  as `false` for every type, which is a definite answer the panel acts on. It named four of
  the six values in `ProviderCapability` and had never been compared with `PROVIDER_TYPES`.

  `certificates` was absent, so `npm` and `zoraxy` read as holding no certificates. `proxy`
  did not list `cloudflare_tunnel`, so the tunnel option left the expose form, which derives
  its tunnel list by filtering the proxy list. Worst was `supports_auto_public_target`, also
  absent, which made Cloudflare and deSEC read as unable to resolve a public target on their
  own. The form hides the "update the DNS record automatically" switch when that is false —
  and rewrites `public_target_mode` to `manual` with `auto_update_dns: false` in the payload
  it sends, and writes both into the form state the moment the DNS provider is selected. So
  reopening a route that had automatic updates on while this read was failing, changing a
  port, and pressing save revoked the setting in the database. The switch was never on the
  screen, no error was either, and the rest of the section looked right, because
  `public_dns` did have an entry and Cloudflare was still treated as external DNS.

  The table now agrees with `app/providers/factory.py` in all fifty-two declarations.

- **The integrations page hid a failed catalogue read behind the names it fell back to.**
  `GET /providers/types` is the catalogue that turns a stored slug into a name — `npm` is
  "Nginx Proxy Manager" there — and it carries `read_only`, the only place the page says an
  integration cannot be written to. The page read it as `typesQuery.data || {}` and never
  looked at `isError`. The empty map is the right floor and is not the defect: `lib/providers`
  groups the ten shipped types from its own table, so the sections still held. What the floor
  cannot supply is the label, so every card dropped to its slug, and it has no entry for
  `read_only`, so Traefik quietly stopped calling itself read-only — on a screen that showed
  no error at all, and looked instead like the names had been corrupted. The same query has
  always had an error path in the modal this page opens, which hands it to the type picker;
  the page behind it swallowed it. It now raises the same warning the health checks raise,
  with a retry that re-reads the catalogue on its own — "Test all" refetched it, but only
  alongside a live connection test against every enabled integration.

- **The certificates page blamed an empty table on integrations it had never read.**
  An empty table explains itself by naming the integrations it queried, and two reads stand
  behind that sentence: `GET /providers` for the names and `GET /providers/types` for which of
  them expose a certificate store at all. Neither was checked. A failed types read left the
  capability list `null`, the last rung defended against that with `?? []`, and the plural rule
  picks the "many" form for zero — so the page printed "These integrations were queried and
  returned nothing: .", a plural naming nobody, a dangling colon, and a claim that a query had
  happened. A failed providers read left that same list empty instead, which the rung above
  reads as "No integration exposes a certificate store": a statement about what is configured,
  made by a page that had just failed to find out. The page now says what it knows — that it
  could not look — and its retry re-reads the capability map, which the refresh never did, so
  the one failure it offered to clear was the one failure it had no way of clearing. A table
  with rows is never held back for either read: a row carries its own integration name, and
  the provider list behind it only prettifies that name.

- **The wizard's last screen ticked "No services found to import" over a scan it never ran.**
  The step an operator stopped on is kept in the session; the scan that fills that screen was
  not. It was fired by the single transition that leads there, so a reload landed on the import
  step with nothing in flight and nothing ever scanned, and the screen's ladder fell through to
  its last rung — a green tick reading "No services found to import" — on the one screen
  whose primary button ends setup. That is the exact claim this file already refuses to make
  for a scan that failed, and a scan that never ran has no more right to it. The same rung
  answered for a provider list that could not be read, too: a failed read leaves the list
  empty, so the screen said "No providers configured", a statement about what is configured
  made by a screen that had just failed to find out. The scan follows the step now rather than
  the transition that used to lead to it, the screen tells a read still in flight from one that
  failed, and the single retry it offers reruns whichever of the two reads was the one to fail.
  No new wording was needed: `setup.import.scan_failed_hint` already says Vauxtra could not read
  your integrations and asks for a retry before finishing.

- **A reload on the wizard's integration step came back to "No integration yet", for good.**
  The setup wizard is meant to survive a refresh: the step it stopped on is session-persisted,
  and the file's own docblock calls that load-bearing. The list of integrations was not. It
  was a `useState([])` filled by one imperative fetch on the password → providers
  transition, and nothing read it again on mount, so a reload restored the step without the
  list. An operator who had just connected three integrations came back to a screen saying
  there were none, and it did not stop at the sentence: the footer button turns into "Skip for
  now", the import step skips its scan on an empty list and reports "No providers configured",
  and the closing summary counts zero of them. This was also the last of ten call sites not
  reading `['providers']` through the shared cache, which is why the three invalidations this
  file already fires reached every screen except its own. It reads the cache now, and the step
  tells apart a list still being read, one that could not be read, and an instance that really
  has none of them — only the third of those is allowed to say "Skip for now".

- **Three panels stated that an instance had nothing while the lists behind them were still in
  flight.** The webhook tab, the Docker section of the data settings and the monitoring drawer
  all read their rows off `data ?? []`, so what an operator sees on a cold load is the same
  empty array an instance with nothing in it has. Each printed the second reading: "No webhooks
  configured", "No Docker endpoint" with a button to add one, and — in the drawer opened by a
  deep link — "No timeline data yet for this host" and "No recent logs linked to this
  hostname". None of it is a flash: the panel's query client retries once, so a failing read
  holds that sentence for the length of a backoff before the error alert replaces it. Four tabs
  on the very same settings screen already paint a skeleton at that rung, and
  `useDockerDiscovery` states the principle in its own comment — about the failure half, which
  was the only half it guarded. The four missing rungs are now there, and the two requests the
  drawer does not own are passed in by the page that already owns them.
- **A filter nobody could name hid most of the routes while the control denied it was on.**
  The tag and environment filters live in the address bar, so `?tag=5` is applied before the
  tag list has been read, survives a reload, and outlives the tag itself. The rows are
  filtered on the id and do not care, but the `<select>` needs an option carrying that value
  to display it, and had none — so it fell back to its first option, "All tags". Measured
  with `/tags` failing and `?tag=5` set: every route hidden, the counter at 0, and the one
  control that could have accounted for it saying no filter was applied. The page read as an
  instance with nothing in it. An applied filter now always has an option of its own, and it
  says which of the two things happened, by the same rule the webhook scope field already
  keeps: an id missing from a list that came back is "Tag #5 (deleted)"; an id missing from a
  list that never came back is "Tag #5", which names nothing it has not read.
- **A settings tab announced that the instance had no integrations before it had asked.**
  The webhook scope field closes "Specific provider" when there is nothing to point at, and
  said why: "No enabled integration to point at yet." It read that off an empty list, which
  is also what every first paint holds and what a failed request leaves behind. So the tab
  stated it as a fact about the instance on every single load, and went on stating it for
  good once the list could not be read. The rule was already written down and obeyed a
  hundred lines below in the same file, where a webhook's target is only called deleted once
  `isSuccess` says the list is the answer. The option still closes in all three cases —
  every one of them leaves nothing to save, and saving would mute the rule — but the three
  no longer share one sentence.
- **A page that could not reach its health checks looked like a page where all was well.**
  The integrations page reads two health endpoints, and neither query's failure was ever
  looked at. When they fail every integration is `unknown` — the honest verdict for a
  measurement that did not happen — but `unknown` is what neither filter counts, so a full
  list published `Issues 0` and `Healthy 0`, and the warning badge in the header went away.
  Anyone arriving by the dashboard's own "Integration health could not be checked" link
  landed on the one page that contradicted it. The failure is now stated above the filters
  whose zeros it explains, with a Retry that takes both readings again.
- **The change-password screen dropped its API-key warning when it could not count them.**
  The count was `data?.length ?? 0`, so a failed request and an instance holding no keys
  were the same zero — and the only sentence saying that a password change leaves existing
  API keys working, together with the link to go and revoke them, disappeared exactly when
  nobody could confirm whether any existed. It now reports that the list could not be
  loaded, and keeps the link.
- **One hung build could leave every later push unscanned, for six hours.** No job in any
  workflow declared `timeout-minutes`, so the ceiling in force was GitHub's default of six
  hours. That would concern only the run that hangs, except `Build & Publish` keeps
  `cancel-in-progress: false` deliberately — two runs there would be two writers on the same
  registry tag or the same release page — and it is the only place three of the five checks
  `main` and `dev` require are ever produced on a push: `Dependency audit`,
  `Image build & scan` and `Vulnerability scan (Trivy + Grype)` reach one through its gate
  jobs and through nothing else. A queue behind a hung build is therefore a queue of
  unscanned commits. `06a90ed` sat in `Build and push` for 48 minutes against a measured 402
  seconds, with two pushes stacked behind it and neither of them scanned. Every job that
  occupies a runner now declares a ceiling, each set far above its measured time so that a
  slow but healthy run still passes, and a test fails the build if a new job arrives without
  one.
- **An integration nothing had measured was published as a green "Active", on the very page
  the dashboard sends you to when the health check fails.** `getOperationalStatus` answered
  `active` for `score < 0` and for `score >= 80` on one line, so no evidence and the best
  evidence there is were painted the same word in the same green. That is the state of every
  enabled integration between the list arriving and `GET /providers/health` answering, and
  the state they stay in for good when that request fails, because the page never reads the
  query's error. Every other reader of the same cache entry already disagreed:
  `getProviderSeverity` returns `unknown`, so the "Healthy" count left those cards out and
  the "Healthy" filter hid them; the dashboard's glance tile showed an "Unknown" chip; and
  the dashboard raised "Integration health could not be checked", whose link points here.
  The chip now says "Unknown" in neutral — the word the rest of the product already uses for
  this state, reused verbatim in all eight languages — and the green is kept for a reading
  that actually happened. Neutral rather than the dashboard's amber, because this is also the
  ordinary first-paint window and an alarm that fires on every load is an alarm nobody reads.
- **Twenty-one locale keys reached the page through a field neither gate could read.** Two
  checks enforce this one rule — `check-locale-usage.mjs` in `Frontend (Node)` and
  `EveryKeyTheUiAsksForExists` in `Backend (Python)` — and both matched only a name written
  out inside the `t(` call. The settings tabs and groups, the three theme buttons and the
  operational chip on every integration card do not write that: they carry
  `labelKey: 'settings.tab.general'` in an object and hand it to `t()` a file away, and
  fourteen of those keys are reached by no `t()` literal anywhere. They are literals and need
  no guessing, which is the one thing these gates ask of a key, so both read them now — 1838
  keys checked became 1859 on the Node side. A typo in any of them fails the build instead of
  printing `providers.status.actve` into the chip, in eight languages, with every check green.
- **A key named as an example in a comment failed the build.** The same two gates disagreed
  in the other direction too: the Node one blanks a line opening with `//`, `*` or `/*`
  before reading it, and says why — prose shows keys, and failing a build over a sentence
  teaches the next reader to stop writing examples. The Python one read prose as code. That
  went unnoticed because the one illustrative key in the tree, in `ConfirmDialog.tsx`,
  happens to name a string that exists. The Python scan now blanks the same lines by the
  same rule, and a written-out sample keeps it honest.
- **A provider you switch off was published as "Failing", on a card that already said
  "Disabled" one chip to the left.** `getHealthScore` clamped the score of a disabled
  provider to 30, which lands in the `error` band, so `ProviderCard` drew a red
  "Failing · 30" badge beside the neutral "Disabled" chip on the same row. The clamp had no
  other reader: `getOperationalStatus` and `getProviderSeverity` both answer on `enabled`
  before they ever look at the score, so painting that badge red was the only thing the line
  did. It also made the badge unreachable — the Integrations page counts issues and filters
  on `getProviderSeverity`, which calls the same provider `disabled`, so the counter said
  zero and the Issues filter hid the card. The red verdict existed only on the screen that
  said nothing was wrong. A provider that is off now reports `unknown`: whatever the last
  signals said, they were gathered while the switch was on, and there is no live reading of
  something that is off. The card's own render decision moved into a shared
  `showsHealthBadge`, and a new test file reads all three answers for the same provider and
  requires them to agree, in both switch positions.
- **Every warning Vauxtra has ever logged read as zero on the only surface an operator can
  alert on.** `add_log` folds the older spelling `warn` into `warning` before the insert, so
  the column holds `warning` — and `/metrics` asked SQLite for `warn`. The bucket those rows
  were in was published as `vauxtra_logs_24h{level="warn"} 0`, and `level="warning"` was
  never emitted at all, so a rule watching for warnings watched a line that could only ever
  read flat. `GET /api/logs` has folded both spellings since the normalisation landed; this
  was the reader that had not. The vocabulary is no longer written out a second time in this
  file either: what the column holds is what gets counted, folded through the same function
  the insert uses, so a level nothing in the product writes is reported instead of dropped.
  The four known levels are zero-filled on top of that, because an absent series and a count
  of zero are the same picture to a person and opposite answers to `absent()`.
- **One family on `/metrics` carried neither `HELP` nor `TYPE`, and two were interleaved.**
  `vauxtra_providers_total` and `vauxtra_providers_enabled` were emitted from a single loop,
  one sample of each per row, which scatters every family's samples through another family's
  — out of spec for the text exposition format, rejected outright by OpenMetrics, and the
  reason `vauxtra_providers_enabled` ended up the only family in the file with no `HELP` and
  no `TYPE` line at all. They are now two passes over the same rows, each behind its own
  header.
- **A label value from the database could end the label set early.** `_gauge` wrote every
  value in quotes without escaping it. Nothing reached it but literals from this file, so it
  never mattered — until log levels started being read out of the column, which is the point
  of the fix above. One unescaped `"` does not spoil its own line: it closes the label set
  and leaves the rest of the scrape unparseable. Backslashes, quotes and newlines are now
  escaped the way the format asks.
- **The metrics table in `docs/HOWTO.md` was wrong in nine places, and nothing could say
  so.** It gave `vauxtra_providers_total` a `state` label it has never had; named a family
  `vauxtra_webhook_deliveries_total` where the code says `delivery`; gave
  `vauxtra_uptime_events_24h` a label called `event` carrying `up` and `down` where the body
  says `status` with `ok` and `error`; offered log levels `warn` and `debug` and neither `ok`
  nor `warning`; described webhooks as `enabled`/`disabled` where the two members are `all`
  and `enabled`; omitted `vauxtra_services_enabled` and `vauxtra_providers_enabled`
  entirely; said nothing about the `status="all"` roll-up sitting inside its own family, so
  `sum()` over that family answers with exactly twice the truth; and printed an example
  scrape carrying a line the endpoint has never emitted. The alerting example was named
  `VauxtraCertExpiringSoon` and annotated "certificate may be expiring" over an expression
  counting every error line in the journal, so a failed NPM call fired an alert about
  certificates — and there is no certificate series to point it at, because reading one
  means calling each provider over the network and a scrape must never wait on a third
  party. The page is rewritten from a measured body, the absence is stated rather than
  implied, and a test now parses that table and compares it to the endpoint in both
  directions: every family published must be documented, every family documented must be
  published, the label names must match, and a closed list of label values must be exactly
  the set that appears.
- **The dashboard drew a lapsed certificate in the same amber as one falling due next
  month.** `expiring_soon_count` is one figure built from two states — still valid but
  inside the warning window, and already past expiry — because both need renewing. Three
  surfaces read it and all three presented it as the first. The sidebar badge went amber
  with no glyph beside it, which states an incident in colour alone. The dashboard tile
  went amber under a hint naming the size of the whole estate, below a value that was not
  about the estate. The "needs attention" row said "3 certificates expire within 30 days"
  and then carried a hint admitting some of them already had. The certificates page one
  click away has always drawn those rows red, so an estate with two hosts serving a
  certificate error to every client read calmer on the dashboard than a single failing
  probe. All three now split the figure through one reader, `certificateUrgency`, built on
  the same buckets the certificates page uses: the total stays the route's own, what has
  lapsed is counted off the rows, the tile and the badge turn red, and triage shows two
  rows instead of one sentence and an apology. The MCP tool description says the same, so
  an agent reading `get_certificate_expiry` no longer reports the merged figure as a
  deadline.
- **A certificate expiring tomorrow could be counted by nobody.** A proxy hands back
  `expires_on` as whatever its own storage layer formats, and three things in Vauxtra read
  that one string: the `/api/certificates/expiry` route, whose `expiring_soon_count` is the
  sidebar badge, the dashboard tile and the "needs attention" entry; the scheduler's expiry
  scan, which chooses between a CRITICAL and a WARNING line in the activity log; and the
  certificates page, which draws the countdown. The route accepted three spellings of a
  date. The other two accept ISO 8601 in essentially any shape. So a certificate whose date
  arrived as `2027-01-01T00:00:00+00:00` — what `datetime.isoformat()` writes and what RFC
  3339 asks for — or with a space instead of the `T`, was read by the log and by the page
  and by nothing else: the badge stayed dark and the dashboard reported a clean estate while
  the journal filled with CRITICAL lines about it. All three now read it through
  `app.expiry.parse_expiry`, one rule in one place.
- **One certificate stamped with a negative UTC offset ended the expiry scan for the whole
  estate.** The scan stripped a `+` offset but not a `-` one, so `fromisoformat` handed back
  an aware datetime and the subtraction that followed — outside the per-certificate guard —
  raised `TypeError`. The scan's outer handler caught it, which meant every remaining
  provider and every remaining certificate went unchecked, on every run, and the only trace
  was one `[CertExpiry] Check failed` line. Offsets are now converted rather than stripped,
  so the instant survives and the comparison stays naive UTC.
- The route answered a `500` for the whole page when a provider sent something that was not
  a string at all: `strptime` raises `TypeError`, not `ValueError`, so the format loop's
  `except ValueError` did not catch it. One unreadable date is now one unknown row.
- That failure handler wrote its own traceback through a second database connection while
  the scan could still be holding an uncommitted write on the first, so the record of why
  the scan stopped could itself fail on `database is locked`. It logs on the connection it
  was given.
- **The alert about a certificate that had already expired said it "expires in -47 days".**
  The scheduler's expiry scan writes one line per certificate into the activity log, and
  every line was built from `expires in {plural(days_left, 'day')}`. Past the expiry date
  `days_left` is negative, so the CRITICAL alert about the one state that is not a
  countdown — a certificate serving a browser warning to every visitor right now — came out
  as `expires in -47 days`, and the one that lapsed yesterday came out as `expires in -1
  days`: wrong tense, wrong sign and wrong agreement in the same six words. The certificates
  page, reading that same certificate, was at the same moment describing it correctly as
  `Expired 47 days ago`, so the two halves of the product disagreed in writing about the
  most urgent thing either of them had to say.
- The count was overstated as well as negated. `timedelta.days` floors, which is the right
  direction ahead of expiry — three and a half days left is stated as three, and nobody is
  told they have longer than they do — but the same floor run backwards turns a certificate
  that lapsed 47 days and two hours ago into `-48`. Each side is now measured by its own
  subtraction, and `app.text.time_to_expiry` picks the sentence: `expired 47 days ago`,
  `expired 1 day ago`, `expired less than a day ago`, `expires in less than a day`,
  `expires in 3 days`. The sub-day phrasings are new; `expires in 0 days` was the previous
  answer for a certificate with twenty hours left, and it states the opposite of the urgency
  the alert is raising. Nothing parses these lines, so only the reading changes.

- **A certificate store that could not be read vanished from the page without a word.**
  `GET /api/certificates/expiry` contacts every enabled integration that keeps a certificate
  store, and wrapped each one in a `try/except` that logged the failure and moved on. The
  page is built entirely from the rows that came back: the total, the five counters, the
  table and the integration filter. So a proxy that was down, unauthenticated or simply slow
  to answer produced a page that looked complete and was not — an estate whose only
  certificate expiring this week sat behind that proxy read exactly like an estate with
  nothing to renew, and the only trace was one line in the activity feed nobody had a reason
  to go and look at.

  The route now answers with `unreachable`, naming the stores it could not read, and the page
  draws a warning above the counters saying the numbers below cover only the rest. Only the
  integration's id, name and type travel: a provider error routinely carries the console URL
  and sometimes the credential that failed, so the error itself stays in the journal. The
  fallback route `GET /api/certificates` is unchanged — it returns a bare list with nowhere
  to put the field, and the page already tells the operator it is running degraded whenever
  that route is the one answering.

- **Two filters on the certificates page could hide every row without showing they were on.**
  The integration filter is component state holding a provider id, and the list it chooses
  from is rebuilt from whichever integrations answered. When the one it names stops
  answering — removed, disabled, or simply unreachable — the id stays in state and keeps
  dropping every row, while the control that set it either renders blank, because no
  `<option>` matches its value, or is not rendered at all, because it is only drawn above two
  integrations. `resolveProviderFilter` now reconciles the id against the integrations
  actually offered, exactly as `toCertFilter` already reconciles the status in the address
  bar, and the page filters, labels and clears itself from the reconciled value.

  `matchesSearch` in the same module expected its needle already lowered; the function of the
  same name in `features/services/helpers.ts` lowers its own. The single caller happened to
  lower it, so nothing was wrong on screen — but one name under two conventions is a search
  that silently matches nothing the day a second caller passes the box contents straight
  through, which is what the other spelling accepts. It now trims and lowers its own needle,
  which is what every other search on the panel already does at the point of use.

- **A certificate whose expiry date the provider could not read was drawn in red as expired.**
  Zoraxy answers `RemainingDays: -1` for a certificate it could not date — its own fallback
  certificate, typically — and the same `-1` for one that expired yesterday. The number alone
  cannot tell those apart, and the panel believed it. `/api/certificates/expiry` already
  answered honestly for that row: no date to count from, so `days_remaining: null` and
  `expired: false`. The table read past that null to the provider's own countdown, reached
  `-1`, and filed the row under expired — a red "expired 1 day ago" badge beside a column
  reading "no expiry date", counted in the expired tile, sorted to the top of the list. The
  dashboard card and the sidebar badge, which read the backend's count, said nothing was
  wrong. Two screens, two verdicts, and the loud one was made from a sentinel.

  Both halves are fixed, because either one alone leaves the other free to invent the number
  again: `app/providers/zoraxy.py` no longer forwards a countdown with no date behind it, and
  `certDays` no longer trusts a provider-stated count unless the row also carries a date it
  could parse. The rule is not that `-1` means unknown — it is that a count with no date
  behind it is not a measurement, whatever its value. A `-1` that does arrive with a date is
  still a `-1`, and still expired. The row now reads unknown, which is what is known about
  it. The module's arithmetic gets its own tests with this, pinning which of the three
  numbers a row can carry may be believed, and in which order.

- **Saving an exposure as a template kept the tags and dropped the environments.** The expose
  wizard offers one label control with two halves: the tags a service carries, and the
  environments it is set to. "Save as template" read the first half and not the second. The body
  it sent listed `tag_ids` and no `environment_ids`; `TemplateIn` had no such field to receive
  one; `service_templates` had no column to store it. None of those three floors said so. The
  route answered 201, the template appeared in the list, and it named no environment — which
  is exactly what a template where none was chosen looks like. The next service built from it
  started one label short of the exposure it was copied from, and the only way to notice was to
  remember what had been on screen.

  `environment_ids_json` is added by migration with a `'[]'` default, so every template written
  before this reads back as naming no environment, which is what it named. The two halves are now
  one `_LABEL_COLUMNS` table in `app/api/templates.py`, and everything that used to spell out the
  tag half — reading the row, writing it, checking the ids exist, dropping the ids whose row is
  gone — loops over it instead. A second half maintained by hand beside a working first half is
  how this happened once already.

  `TemplateIn` also refuses unknown keys now (`extra="forbid"`), the rule `ServiceIn` already
  applies and for the same reason: a field the model does not know is a 422 on the save, not a
  template quietly missing it. That is the half of this fix that catches the next one.

  The panel follows the model. The template form grew an Environments section beside its Tags
  one — written once as a `LabelSection` and used twice, because a section that existed for
  one half and not the other is how the environments came to be dropped. The template card shows
  both halves in one chip row, the Templates page offers a filter row per half, and
  **Settings → Taxonomy** counts the templates naming an environment where it used to count
  only tags and tell you, wrongly, that deleting an environment had no template to affect. The
  two filter rows keep separate active lists: a tag and an environment may share a name, and the
  server only refuses a duplicate within one kind.

  That last sentence is also why the chips changed colour. A template chip used to take its
  tone from the tag's own colour, which the Services list had already decided against: it
  gives the two halves nothing to be told apart by, and two labels sharing a name and a colour
  are then the same chip. Labels now read the same way everywhere, out of one
  `frontend/src/lib/labels.ts`: the tone says which half it is, and the label's own colour
  rides on the dot in front of the name.

- **A tag or environment whose name held a comma or a colon came back as a different label.**
  Every service read serialised its labels as `GROUP_CONCAT(DISTINCT t.name || ':' || t.color
  || ':' || t.id)` and took the answer back apart on `,` and then on `:`, keeping the chunks
  that came out in exactly three pieces. The name is the first of those pieces, so the name was
  the one field able to move the boundaries it was being read between — and nothing refuses
  either character. `TagIn` and `EnvironmentIn` strip the name, refuse it empty and stop it at
  32 characters, and that is the whole rule; the panel offers a free text field, and `web:prod`,
  `a,b` and `staging: eu` are names an operator types without a second thought.

  Measured before the change, with six tags on one service named `prod`, `a,b`, `web:prod`,
  `a,`, `x:` and `zeta`, `GET /api/services` answered with four: `prod`, `b`, the empty string,
  and `zeta`. Two names vanished, and two came back under the right id and a name nobody typed.
  The second half is the worse one: a chip that goes missing is visible, while a chip reading
  `b` over a tag called `a,b` is not, and `GET /api/tags` holds no `b` to reconcile it against.
  The damage stopped at the label that caused it — each label contributed one comma-separated
  run, so `prod` and `zeta` came back untouched — which is why nothing about the row looked
  wrong enough to investigate.

  The three queries in `app/api/services.py` no longer join the two label tables at all. A new
  `labels_by_service` reads the links as rows, one query for tags and one for environments,
  both keyed by service id, exactly as the push targets are already read on the same route;
  rows carry their own boundaries, so there is nowhere for the fault to happen. `GROUP_CONCAT`,
  `parse_tags`, `parse_environments` and the `GROUP BY` that existed only to undo the fan-out
  are gone with them, along with a `tags_raw` parameter on `row_to_service` that all three
  callers had already stopped passing. Labels now arrive ordered by name, which is the order
  `GET /api/tags` and `GET /api/environments` already answer in and which `GROUP_CONCAT
  DISTINCT` never promised.

- **Deleting a label said nothing about what it took with it, and left no trace once it had.**
  "{name} will be removed from every service that carries it" was one line, for both taxonomies
  and both kinds of holder. It named no number, so a tag on one service and a tag on forty asked
  the same question — over the two lists in Settings that are the easiest thing in the product
  to delete by accident. Every other deletion here counts what it is about to change: a provider
  names the services, the templates and the webhooks that point at it, a root domain names the
  services and the templates built on it.

  The second half was not said at all. A tag is held in two places that behave nothing alike.
  `service_tags` declares `ON DELETE CASCADE`, so the services are unlinked on the spot: they
  keep their hostname and stay published, and lose only the label somebody was filtering and
  grouping by. `service_templates.tag_ids_json` is TEXT holding a JSON array, which no
  constraint reaches — the id survives the delete and is dropped on the next read by
  `_drop_dead_labels`, silently and by design, because the alternative is a template that
  cannot be saved. So the template comes back one tag shorter, the next service built from it
  starts without the tag, and nothing anywhere says why. An environment had only the first of
  those when this was written, having no JSON column to rot; the entry above gives it one, and
  both paragraphs are now said over both halves.

  Each chip now carries the number of services holding it, and the delete question says the two
  paragraphs above over the rows behind that number — capped at five, with a tail counting the
  rest, through the same `DependentList` the root-domain dialog uses so the two caps cannot
  drift apart. The plain question is kept for a label nothing holds: asking the long one over a
  tag created by mistake a minute ago is how a confirmation stops being read.

  `DELETE /api/tags/{tid}` and `DELETE /api/environments/{eid}` were also the only destructive
  routes in the API writing nothing to the journal, and this is the one deletion whose damage
  cannot be reconstructed afterwards — the id is gone and the link rows went with it in the same
  cascade. Each now writes one line that names what it unlinked rather than counting it, since
  by the time the line is read there is nowhere left to resolve a number. The two MCP tools say
  the same thing in their docstrings, so an agent knows to call `list_services` and
  `list_templates` before `delete_tag` rather than after.

- **One route answered under two cache keys with four different sets of options, and the
  events that change the answer refreshed only one of the keys.** `GET /api/auth/me` says who
  the caller is and whether the instance has a password at all. Six components read it: the
  boot gate, the layout banner, the sidebar and the dashboard under `['auth-status']`, the two
  settings tabs under `['auth-me']`. Of the eight places that invalidate once that answer could
  have changed, five named `['auth-status']` alone — signing out, signing in, finishing the
  setup wizard and tripping the 401 interceptor each left the other key's entry where it was.

  What kept that from being visible is the other half of it. The settings copies declared no
  `staleTime`, which means zero, so both tabs refetched on every mount: opening Settings drew a
  skeleton and spent a round trip on an answer the shell was already holding fresh, and that
  round trip is what hid the stale entry behind it. The four declarations disagreed among
  themselves as well — 60 s and `retry: false` in the boot gate and the banner, 120 s in the
  sidebar, 120 s and `retry: false` in the dashboard, and the two settings copies inheriting
  the client's `retry: 1`. A `staleTime` is per observer while the entry is shared, so the
  shortest one decides when everybody refetches, and a `retry` is resolved by whichever
  observer happens to be the one fetching.

  There is one hook now, `frontend/src/hooks/useAuthStatus.ts`, holding the key, the query
  function, a one-minute window and `retry: false` — one entry behind one hook, the arrangement
  `hooks/useProviderTypes.ts` already had. It writes them into an exported `queryOptions()` so
  that the setup wizard's pre-paint `fetchQuery` can be handed that same object and can no
  longer seed the entry under different terms than the hook reads it under. Outside the tests
  that stub the client, `'auth-status'` and `'/auth/me'` are each now written once in
  `frontend/src`. `AuthMe`, a dead alias of `AuthStatus` with no usages anywhere, went with it.

- **Three files read `GET /api/certificates/expiry` and declared three different answers, and
  the certificates table drew an "issued by" chip from a field no provider has ever sent.**
  The chip was the visible half. `CertificateTable.tsx` guarded it with `cert.issuer && …`,
  and `issuer` is written nowhere in `app/` — not by the NPM provider, not by Zoraxy, not by
  the route that assembles the payload — so the guard has always been false and the chip has
  never rendered once. Its string sat in all eight locales as `certificates.issuer`, and the
  login page advertised the feature in `login.hero.certs_body`: "Expiry dates and issuers, so
  a renewal never surprises you again." The key is gone and the login line now promises days
  remaining, which is what the payload actually carries.

  Behind the chip was the reason nobody noticed. `CertificateRow`, declared privately in
  `certificates.ts`, was wider than the route on every axis: fifteen of its sixteen keys
  optional, plus `issuer` and a legacy `provider` that no route sends. `Certificate` in
  `types/api.ts` said `id: number`, right for NPM and wrong for Zoraxy, which keys its
  certificates by file name and has the string passed straight through. And the sidebar had a
  third spelling, `CertExpiryResponse`, naming the one key it reads. Three declarations of one
  payload, two of them disagreeing about six keys — `expires_on`, `days_remaining`, `expired`,
  `expiring_soon`, `provider_id`, `provider_name` — required in `types/api.ts` and optional in
  `certificates.ts`. There is one declaration now, in `types/api.ts`, re-exported from
  `certificates.ts` so that nothing importing it had to move.

  `DockerContainer` had drifted the same way in the other direction. The shared copy claimed a
  `ports` array of `{private_port, public_port?, type}` that no handler has ever assembled, and
  left out eight keys `GET /api/docker/containers` puts on every row: `target_ip`,
  `target_port`, `suggested_subdomain`, `suggested_scheme`, `websocket`, `endpoint_id`,
  `endpoint_name` and `existing_service`. It also made `suggestion` nullable, though
  `analyze_container` ends in a port heuristic that always succeeds and the handler attaches
  the result unconditionally. `useDockerDiscovery.ts` carried a second, correct copy — which is
  exactly why the discovery panel worked while the shared type was wrong, and why the wrong one
  was the copy sitting in the file a reader opens first. `ContainerSuggestion` beside it said
  `target_port: number` where the heuristic returns null, offered a `'none'` source against the
  analyzer's three, and was missing `websocket`, `middlewares` and `tls_resolver`.

  Four more private declarations shadowed a shared name while disagreeing with it. The
  sidebar's own `AuthStatus` made `setup_required` optional; `app/api/auth.py` computes it as a
  bool on every answer, so the optional spelling only ever bought a null check nothing needs.
  `expose/types.ts` held a `Provider` with five keys of eleven. `providerConstants.ts` held a
  `ProviderValidationResult` carrying neither `detail_code` nor `detail_params` — the two
  fields the route has sent since validation diagnostics were translated — and a `GuidedStep`
  colliding with the shared name while meaning something narrower; the panel's wizard types are
  `WizardStep` and `WizardField` now. `Layout.tsx` read `/auth/me` through an inline
  `{ auth_mode?: string }` and takes the shared `useAuthStatus()` hook instead, and
  `ProviderItem.type` in `setup/types.ts` was `string` where `ProviderType` is the enumeration
  it has always held.

  All of it was found by `scripts/check_read_contract.py` on its first complete run: fourteen
  divergences and five shadowed names on the commit before this one. Two of those fourteen were
  the `AuthStatus` shadow arriving from the other side, once under its route and once under its
  cache key, which is the argument for running both rules — a name collision and a disagreement
  about bytes are one defect described twice.

- **A webhook asked for disabled was created enabled, and answered `201` saying so.** The panel
  has sent `enabled` since that form existed and `WebhookIn` never declared it, so pydantic
  dropped the field before the handler saw it and the `INSERT` wrote a hardcoded `1`. The reply
  was hardcoded to match the row rather than the request, which is the shape that hides
  longest: the answer agrees with the row, the list reads the row back and draws the toggle on,
  and the only way to notice was to send `false` and watch it not take. No caller sends `false`
  today — the panel's own form omits the key and the wizard passes `undefined` — so nothing was
  mis-created in the field; what was broken was the contract. `WebhookIn` declares the field
  now, defaulting to `True`, so every caller that omits it keeps exactly the behaviour it had,
  and `create_webhook` in the MCP bridge gained the flag as well, for a target configured
  ahead of the migration that will need it without firing in the meantime.

  The TypeScript `ProviderUpdate` advertised `type`, which `PUT /api/providers/{pid}` neither
  declares nor writes — a provider stays whatever kind it was created as, and the route reads
  the stored `type` only to normalise the URL. Nothing sent it, so nothing was lost; what
  existed was a trap with `tsc` holding the door open for whoever wrote the next editor. It is
  now `Omit<Partial<ProviderIn>, 'type'>`, which refuses the field at the keyboard.

  Both were found by `scripts/check_panel_contract.py` on its first complete run.

- **A provider could be renamed to nothing, and switched to a value that is neither on nor
  off — leaving it drawn as connected and used by nothing.** `PUT /api/providers/{pid}` is
  the only route that writes these two columns after creation, and it guarded neither.
  `ProviderIn` refuses a name that is only spaces, in those words; the update read
  `body.name or row["name"]`, so `""` fell through to the stored name while `"   "` was
  truthy, reached `.strip()`, and renamed the provider to the empty string. Two spellings of
  the same empty answer, one kept and one destroyed.

  `enabled` was declared `int`, and it is not a number — ten queries across the API and the
  scheduler read `WHERE enabled=1`. A provider saved with any other integer matched none of
  them: no sync, no certificate lookup, no Docker discovery, absent from the multi-sync target
  list. `GET /api/providers` still listed it and the panel, which reads the field through
  `Boolean()`, still drew it as on. Nothing in the panel or the MCP bridge can produce either
  value — the editor trims the name and disables Save when the trim is empty, the toggle sends
  `1` or `0`, and the `update_provider` tool has always declared a boolean — but the route is
  reachable directly, and the column it wrote is read by everything downstream.

  The name is now refused in the sentence the create already writes, and `enabled` is declared
  the boolean it is, as `WebhookUpdateIn` next door always has been. A row left holding an
  out-of-range value by an earlier version is put back in range by its next save rather than
  carried forward. An absent field still means "keep what is stored", which is what this route
  is for: the toggle sending `{"enabled": 0}` alone still leaves the name, URL, username and
  stored password exactly as they were.

  This divergence was written down. `ALLOWED_CONTRACT_DIVERGENCES` in
  `scripts/check_api_mcp_parity.py` carried an exemption for it whose own note said an `int`
  in the schema "would invite an agent to send 2 -- which the route would store" — and then
  exempted it. The note was also wrong in the safe direction: it ended "nothing would ever
  read as anything but truthy", when the ten `WHERE enabled=1` readers read it as false. The
  exemption is gone, and the gate refuses to keep one that no longer matches anything, which
  is how removing it was noticed at all.

- **A body value of the wrong type answered `500`, and a body missing its one key deleted
  every alert rule of a service and answered `ok`.** Seven write routes read their body as a
  plain `dict` and reached straight for what they wanted. `.strip()` on a number, `int()` on a
  word and `.get()` on a string all raise, nothing above them catches it, and the caller read
  `500 Internal Server Error` for a mistake in their own payload: sixteen distinct ones were
  measured. The panel produces none of them, because it types its own state and clamps its own
  numbers before it posts. What reaches these routes unshaped is `vauxtra_mcp`, where
  `create_webhook`, `update_webhook`, `test_webhook_url`, `set_service_alerts`, `add_domain`,
  `create_environment` and `update_environment` pass their arguments on as JSON, and the thing
  composing that JSON is a language model.

  `POST /api/services/{sid}/alerts` is the one that cost something. It replaces every rule of
  the service, and it read its list with `body.get("alerts", [])`: an empty body, or one whose
  single key was misspelled, deleted every alert rule on the service and answered
  `{"ok": true}`. An entry inside the list that had lost its `webhook_id` was skipped by the
  same reasoning — and skipping an entry after the `DELETE` has run is not "not added", it is
  "removed". Both are silent in the one direction nobody hears about: the service stops
  alerting and goes on looking configured. Both ids are real foreign keys and both were left
  to the index to report, so an id naming no row raised `IntegrityError` as a bare `500`
  after the deletion had already run in the same transaction; the rollback saved the rules
  that time, which is luck, not design.

  All seven now declare their body, so a wrong type is the `422` that names the field. The
  alert list has no default — clearing the rules is still one request, and it is
  `{"alerts": []}`, which says so. The service id and every webhook id are checked before
  anything is deleted, answering `404 Service not found` or naming every unknown webhook at
  once, with the existing rules untouched in both cases. `min_down_minutes` is clamped at zero
  here as it already was on the webhook itself.

  Every refusal these routes had already written by hand is unchanged, and that is the point
  of the shape chosen: the empty name is still `Name and URL are required`, a URL apprise
  cannot parse is still refused by the same validator, and a webhook scope target is still
  answered by `_normalize_scope` in the four different sentences it distinguishes — an unknown
  word, a missing target, a target that is not a number, a number naming no row — rather than
  being flattened into one generic `422` ahead of it. `PUT /api/webhooks/{id}` reads what the
  caller actually sent rather than what has a value, so `{"enabled": false}` on its own is
  still a partial update and leaves the other ten fields as stored.

  `scripts/check_api_mcp_parity.py` compares an MCP tool's payload against the model of the
  route it calls, and had nothing to compare for any of these: routes with a body and no model
  fall from ten to three, and the three that remain are the free-form payloads that cannot
  have one — the two import endpoints and the settings key/value map.

- **A hostname was checked, then published, and only then refused — with a `500` and no
  record of what had gone out.** Both service write endpoints read the table for a service
  already answering for the hostname, then call the proxy and the DNS server, then store the
  row. That is two statements with every provider call sitting between them, so a second
  operator saving the same hostname inside that window passes the same check, and
  `idx_services_hostname` is left as the only thing that still knows — firing once the
  route is already live. Measured on a creation: the DNS record answered for the name and no
  row in the database described it, so there was nothing to list and nothing to delete from.
  Measured on a rename: the provider served the new hostname while the surviving row still
  spelled the old one, which the drift check then reports as wrong for as long as it stands.
  Both answered `500`, and both leaked the connection they refused on.

  Both now answer the same `409` the check itself answers, naming the service that won the
  race, and both write down the one fact the operator cannot recover afterwards — the
  creation names the providers the hostname reached, the rename names the hostname those
  providers now serve. Neither withdraws anything, deliberately: the winner holds that name
  on those same providers, so a withdrawal keyed on the hostname would remove its records
  rather than the orphaned ones.

  Renaming a tag or an environment had the small version of the same gap, with its own
  creation three lines away already handling it — a `500` where the create answers `409`
  for the identical collision. A guard now walks the AST of `app/api/*.py` and fails on any
  write to a `UNIQUE` column that neither settles the clash in the statement itself
  (`INSERT OR IGNORE`, an `ON CONFLICT` upsert) nor sits inside a handler that would take an
  `IntegrityError`.

- **An edit that dropped a provider answered "saved" when the provider refused to let
  go.** Removing a second DNS server from a service's target list, or emptying the proxy
  field in the editor, withdraws that provider's route as it saves. When the withdrawal was
  refused — an expired token, a revoked scope, a server that answered 401 — the failure went
  to the journal and nowhere else: the route answered `200` with `errors: []`, and the panel
  showed the same green *Service updated* it shows for a save that worked.

  It is the one place that did that. `withdraw_service_routes` returns one message per
  failure and its five other callers all pass those messages on — the delete route, the bulk
  bar, the provider deletion, and both halves of the disable withdrawal. This one wrote a
  journal line and stopped. What made the silence permanent rather than merely quiet is the
  next line: the target row is unlinked either way, and `_all_route_holders` reads the rows
  it deletes, so no later push and no later deletion of the service could reach that provider
  again. A hostname went on resolving on a server nothing in Vauxtra addressed. The save now
  names the refusal — `Former target: Failed to delete DNS rewrite on <name>` — in the field
  the editor, the row switch and the bulk bar already read, and a guard reads the source so
  the next call site added cannot journal and stop either.

- **A notification webhook could be armed at a target that was not there.** A rule is stored
  as a word and a number — `scope_type` `service` plus `scope_ref_id` 12 — and the number had
  nothing checking it. `webhooks.scope_ref_id` carries no foreign key, so any positive integer
  was accepted and written: a typo, a stale id from a copied body, an id borrowed from the
  other list. `_service_matches_scope` then answers False for every service forever, while
  Settings goes on showing the rule as enabled. It is the same end state the delete paths in
  `app/api/providers.py` and `app/api/services.py` each already warn about in the journal —
  a webhook that outlives its target is dead and it looks armed — reached instead through the
  door that had no warning at all. Creating or updating a scoped webhook now reads the table
  its word names and answers `400 Nothing to alert on — unknown service 12` before anything
  is stored.

  The second half was worse than dead. The two words share one id space and mean different
  things in it, so changing only the word carried the old number across: a rule watching
  service 4 became a rule watching whichever *provider* holds id 4 — an unrelated row, alerted
  on with confidence. Silence someone eventually notices; a wrong subject reported correctly
  they do not. Changing the word now asks for the new target. The panel already sent both
  fields on every scope change, so nothing in the UI had to move.

- **The per-row switch reported a refused disable as done.** The answer to
  `PUT /api/services/{sid}` carries an `errors` field: what the save could not carry out on a
  provider. The delete button and the bulk bar both read it and raise a warning; the switch on
  each row threw it away, and the `Service` type did not even declare the field. A disable
  whose proxy refuses the suspension now stops rather than deleting a host it was only asked
  to switch off — and that refusal was exactly what the switch announced in green as
  `{host} disabled`, while the name went on answering. It now warns the way the other two
  callers do, naming the first two failures and counting the rest.

- **A hostname spelled with a capital at the provider imported a second service.** Everything
  the editor writes is stored in lower case: `ServiceIn` lowercases the subdomain and
  `normalize_domain` lowercases the domain. The import route stored what the provider spelled.
  Nothing downstream complained, because the public hostname is derived through
  `_service_fqdn`, which lowercases — so a service imported as `NAS.maison.lan` pushed, and
  drifted, under `nas.maison.lan`: the same name as the service already tracking it. The
  unique index on `(subdomain, domain)` compares the stored spelling, so it could not stop the
  duplicate, and `_ensure_hostname_unique_index` could no longer be created on a fresh start.

  Three consequences, all silent. The scan offered the row as new every time, because
  `_already_imported` compared the stored spelling too. Ticking it inserted the second service.
  And a proxy host and a DNS record for one name, spelled differently by their two providers,
  stopped pairing — two services, each holding half of what one should have held, instead of
  one holding both. Names are now compared and stored through one helper, stripped and
  lowercased, the way the padded-name fix already handled the other half of the same question.

- **Clearing a provider from a service left its route live, and unreachable.** An operator who
  moves a service to DNS only, or to proxy only, empties one of the two provider fields in the
  editor. Both blocks that reconfigure a provider on save open with `if body.<...>_provider_id`,
  so emptying that field skipped them entirely: the proxy went on serving the hostname, or the
  rewrite went on resolving it, for a service that no longer named the provider anywhere.

  The same save blanked `npm_host_id`, and `_all_route_holders` reads those very columns to
  decide who still holds a route — so deleting the service afterwards could not reach the
  orphan either, and neither could a disable, a push or a drift check. Nothing in Vauxtra knew
  it existed. A cleared column is now withdrawn from the way a target dropped from the
  multi-sync list already was, which is also how the switch to tunnel mode has always treated
  the two columns it empties. A provider moved from the column into the extras list is not a
  cleared one: it keeps serving the route, and is left alone.

- **The editor's own toggle and a bulk disable still deleted a host that had only refused.**
  The fix below reserved the deletion for providers that genuinely cannot suspend, in the
  withdrawal the push path calls. The two write routes kept the old reading: `PUT` on a service
  with `enabled` turned off, and selecting rows in the table and pressing Disable, both fell
  through from a refused `toggle_host` to `delete_host` — the custom locations, the advanced
  configuration and the certificate binding gone, `npm_host_id` blanked so nothing could put
  them back, and an info line in the journal saying the route had been removed on purpose. The
  batch multiplied it by however many rows were selected. Both now tell the two cases apart the
  way the enable half three lines above them already did, and report the refusal instead.

- **A route the provider spelled back differently was reported missing, and pushed twice.**
  Zoraxy keeps a rule under the spelling it was typed with and AdGuard echoes the name it was
  given, so a route created as `Vault.Example.com` is the route the service spells
  `vault.example.com`. Four readers in the sync layer compared those hostnames and only two
  of them lowercased first. The drift check was one of the two that did not: it reported
  `missing_proxy_route` with a Reconcile button beside it, the push that button fires found
  the host and updated it, and the error was still there afterwards. The DNS reader inside
  the push was the other: it read "no record", added a second one beside the one that was
  already correct, and AdGuard and Pi-hole both hold two rewrites for one name happily. The
  withdrawal fell back to the address Vauxtra last stored rather than the one on the server,
  and deleting a DNS record through the provider page answered 404 on a spelling difference.

  All four now go through the same two comparisons, so they cannot drift apart again.

- **A proxy route that was suspended read as perfectly in sync, and no push could turn it back
  on.** Disabling a service suspends its primary proxy host rather than deleting it — that is
  what keeps the custom locations, the advanced configuration and the certificate binding that
  no Vauxtra column models, and what keeps `npm_host_id` valid. So "held but not served" is a
  state Vauxtra itself produces, and every way back out of it ran through one branch in
  `update_service`: the one that fires only when `enabled` actually flips, only in `proxy_dns`
  mode, and whose `except` writes a warning and lets the route answer 200.

  Everything downstream was blind to the result. The drift check looked the host up by
  hostname and then compared the origin, never the flag Vauxtra had itself written, so a
  suspended route came back in sync. `update_host` is a PUT that does not carry `enabled`, so
  a push — and Reconcile, which is a push — wrote the right origin onto a suspended rule and
  left it suspended. The hostname answered nothing and every screen said the service was
  published and converged.

  Drift now reports it as `proxy_route_suspended`, a push lifts the suspension as it updates
  the host, and the dry-run says `resume` instead of `update` so the plan explains the report
  beside it. Reading the flag costs the plan one listing on the primary proxy that it did not
  make before, which is also what stops it from answering "update" about a provider it cannot
  reach — the way it already behaved for every other proxy in the loop.

- **A failed re-enable was written to the journal as a success.** `toggle_host` returns False
  for two unrelated reasons: the provider has no suspension, or the provider has one and
  refused the call. Both enable paths read every False as the first and logged `Proxy active
  (toggle not supported, host already present)` at info level — which is exactly wrong for NPM
  and Zoraxy, the only two providers that implement it. The host stayed suspended, the row
  read enabled, the route answered nothing, and the one place an operator looks to find out
  what happened said the proxy was active. The two cases are now told apart by the provider's
  class rather than by the wire, and a refusal is an error the route returns.

- **A provider that hiccupped during a disable had its host deleted instead of suspended.**
  The withdrawal suspends the primary proxy host and falls back to deleting it when the
  provider has no suspension to offer — and it decided which case it was in from the `False`
  the call returned. That is the same `False` NPM answers on an expired token and Zoraxy on a
  failed toggle, so a transient provider error during a disable deleted the host along with
  the custom locations, the advanced configuration and the certificate binding the suspension
  exists to keep, and blanked `npm_host_id` so nothing was left to put back. The fallback is
  now reserved for providers that genuinely cannot suspend; a refusal is reported. The
  dry-run already read the class rather than the wire, so the plan and the push agree again.

  Eleven tests cover the suspended route, the push, Reconcile, the dry-run, a refused resume,
  a refused suspension and a provider with no suspension at all; six of them fail on the code
  as it stood.

- **Pushing a disabled service published it again, and the drift check called its absence an
  error.** A push converges the providers on what the record says, and the record is allowed
  to say "off" — but `_push_service_row` never read `services.enabled`. It read
  `providers.enabled` to pick its targets, then created the route on every one of them. Two
  one-click paths reached it: the drift drawer offers Reconcile beside the errors it has just
  listed, and `ExposeModal` fires a push of its own right after saving any service carrying a
  multi-sync target, so pressing Save on a disabled service republished it on every provider
  while the row went on reading "off". The scheduler was the only safe caller, and only
  because `run_auto_reconcile` selects `WHERE enabled=1`.

  The drift check had the same blind spot from the other side. Reading every service as if it
  were published, a correctly disabled one came back "out of sync, 2 errors" — one
  `missing_proxy_route`, one `missing_dns_rewrite` — for routes Vauxtra had itself removed,
  with a Reconcile button beside them whose only honest meaning would have been "publish it
  again". Drift now reads the flag and asks the opposite question of a disabled service: two
  new issue types, `proxy_route_still_served` and `dns_rewrite_still_served`, name a provider
  still answering for a service the table shows as off. NPM and Zoraxy suspend a host rather
  than delete it, so the route stays in the listing and `enabled` is the only thing that says
  whether it answers; a provider that does not report the flag has no suspension, and a rule
  it still holds is a rule still serving.

  A push of a disabled service now withholds it, the way disabling it does rather than the
  way deleting it does. The primary proxy host is suspended, not deleted, so NPM keeps the
  custom locations, the advanced configuration and the certificate binding that Vauxtra does
  not model, and `npm_host_id` stays valid for the re-enable to toggle back on. Everything
  else is removed, because DNS has no suspension and Vauxtra never stores the host ids of the
  extra proxies — a suspension there could never be lifted. A provider with no suspension at
  all falls back to the deletion and clears `npm_host_id` with it, or the re-enable would
  toggle an id that is no longer there, read the refusal as "toggle not supported, host
  already present", and leave the service dark while reporting it switched on.

  The dry-run answers the same way, because it is the documented way to find out what a push
  will write — `docs/TROUBLESHOOTING.md` says to run it first. It used to promise to create
  the route on every provider for a service the push would then remove from every provider,
  which is the one answer a dry-run must never give. A disabled service now gets a plan
  carrying `withheld: true` and one `suspend` or `delete` per provider, and the panel says so
  in a sentence above the list instead of leaving the operator to infer it from the verbs.
  Whether the primary is suspended or deleted is read off the provider class rather than
  discovered by the failed call: a provider that never overrode `toggle_host` inherits the
  base's `return False`, so its suspension could only fail. `dry_run_push`, `push_service` and
  `check_drift` say the same thing in the MCP bridge, which is where a model reads what a tool
  does before calling it.

  `tests/test_multi_sync_targets.py` covers the push, the drift report, the reconcile, the
  dry-run, the return to service and a proxy with no suspension; eight of the twelve new tests
  fail on the code as it stood.

- **Disabling a service took it off the primary proxy and left the second one forwarding it.**
  The write routes learned to publish a service on every multi-sync target and to withdraw it
  from one dropped off the list or renamed away, but the `enabled` flag was never part of that
  walk: `update_service` and the bulk enable/disable addressed `proxy_provider_id`,
  `dns_provider_id` and `tunnel_provider_id` and nothing else. Disabling a service suspended
  the primary proxy host and deleted the primary DNS record while the second proxy went on
  forwarding the same hostname and the second DNS server went on resolving it. The table
  showed the service as off, which is the one state an operator reads as "this is not
  reachable any more", and the only thing that had actually changed was who Vauxtra was still
  talking to. Nothing corrected it later either: the scheduler's push skips a disabled
  service rather than withdrawing it.

  `withdraw_extra_targets` is the mirror of `push_extra_targets` and runs beside it on every
  edit and in the bulk loop, one of the two doing nothing on any given call because a service
  is either published everywhere or nowhere. In the bulk loop it sits above the mode
  branching, where the `continue` for tunnel and unknown modes cannot skip it, and the enable
  half now republishes on the extras it withdrew from -- which the bulk path had never done,
  and only got away with because it had never withdrawn anything.
  `tests/test_multi_sync_targets.py` covers both directions, the edit of a service
  that was already off (the defect the primary DNS block carries a comment about),
  and a service with no extra target, which must not make Vauxtra call anyone.

  The withdrawal has to reach the journal, and twice it did not. `withdraw_service_routes`
  writes nothing but log lines, and it writes them on the caller's connection; the last
  commit in `update_service` sits above `push_extra_targets`, which commits its own, so every
  "record removed on <server>" line the withdrawal wrote was rolled back on close. The record
  really was deleted on the second DNS server and the journal said nothing about it. The line
  also carried `[Delete]`, the prefix its four other callers earn, beside a service that still
  sits in the table -- the primary path says `DNS record withheld (service disabled)` for the
  same reason, and the withdrawal now says `[Disable]`.

  The extras are deleted where the primary proxy is only suspended, and that asymmetry is
  deliberate. NPM's enable and disable live on their own endpoints, so the PUT behind
  `update_host` never clears a suspension; the re-enable path here is `push_extra_targets`,
  which finds the host by hostname and updates it. Suspending an extra would leave it dark
  after every re-enable, and silently, since the PUT answers 200 and the journal reads "Proxy
  synced". Deleting it means the next push finds nothing and creates the host again.

- **Nothing about signing in ever reached the activity log, so a run of wrong passwords left
  no trace at all.** `app/api/auth.py` and `app/auth.py` held no `add_log` call between them:
  the journal that *Recent activity* and **Settings → Logs** read recorded every provider
  sync and every certificate renewal, and said nothing about who had tried to get in. A
  refused sign-in now writes a `warning` line; a successful one, the setup wizard's first
  password, and a password change that ends every other session each write an `info` line.
  `docs/HOWTO.md` lists the four under § 2.

  No client address is written beside them, deliberately. Behind a reverse proxy
  `request.client.host` is the proxy for every caller alike unless `FORWARDED_ALLOW_IPS` names
  the hop allowed to set `X-Forwarded-For` (`app/limiter.py` says why), so an address in those
  lines would be a false lead in exactly the investigation they exist for. Only attempts that
  reach the route are recorded, and that is what bounds the journal: past five a minute the
  limiter answers 429 before the route runs, whereas a line per refused request would let an
  unauthenticated caller fill the table at its own rate. `tests/test_auth_journal.py` covers
  the four lines, that each failure gets one of its own, that a refused password change writes
  nothing, and that no line carries an address.

- **German printed the plural adjective at one wherever a truncated list said how many
  holders it had hidden.** Both delete-confirmation dialogs cut their list of dependants at
  five and finish with a counted phrase, and both wrote that phrase as a single form:
  `providers.delete.deps_more` and `settings.dns.confirm.in_use_more` carried no `_one`, so
  `Intl.PluralRules` fell through to `_other` and German read „und 1 weitere“ where it
  should read „und 1 weiterer“. Six holders is the common case that reaches it — five
  named, one counted. Both keys now carry `_one` and `_other` in the six locales that declare
  a singular and `_other` alone in ja and zh, which is what the four sibling `+{count} more`
  keys already did.

  The gate that should have caught it had been told not to look. `providers.delete.deps_more`
  sat in the quality checker's `DECLARED` map under the reason "the word 'more' does not
  inflect, and neither do its translations" — a claim `expose.toast.more_one` disproves two
  lines of German away in the same file. The waiver is gone rather than widened.

- **Deleting a root domain answered "ok" for a name that was never there, deleted nothing
  when the name was typed the way it reads rather than the way it was stored, and warned
  about breaking routes it cannot break.** `DELETE /api/domains/{name}` was the last delete
  route of eleven still answering without looking its row up: on a database holding no such
  name, `DELETE /api/domains/never-existed.test` returned `{"ok": true}`. And `POST /api/domains`
  stores the name through `normalize_domain` — `  Example.TEST.  ` is kept as `example.test`
  — while the delete compared the path segment raw, so `DELETE /api/domains/Example.TEST`
  matched no row, changed nothing, and answered `{"ok": true}` all the same; the list came
  back with the domain still in it. The MCP bridge hands that receipt straight to an
  assistant, which reports a deletion that did not happen. Both are gone: the route
  normalises the name the way it was stored and raises 404 when no row carries it, and the
  bridge tool stops describing the match as exact when it never was.

  The deletion also wrote nothing to the journal, on a table three other code paths write to
  behind the operator's back. It now writes one line, and the line says what is still built
  on the name: the services and the service templates that hold it, named up to five and
  counted past that.

  The confirmation dialog was the other half, and its sentence was false in the direction
  that causes the wrong decision. It said deleting the domain "may break existing routes".
  Nothing at runtime reads the `domains` table at all — the scheduler, the DNS push and the
  proxy push all work off `services.domain` — so no route goes dark, no record is withdrawn,
  and every hostname stays published exactly as it was. The tab also counted only services,
  so a domain that only a service template named showed the neutral "0 services" badge and
  the same plain question an unused domain gets.

  The dialog now counts both kinds of holder, lists them, and says the three things that are
  true instead of the one that was not: nothing above stops working, the name stops being
  offered in the pickers when a service or a template is created, and the name can come back
  on its own — `INSERT OR IGNORE INTO domains` in the Docker scan and in the provider import
  re-adds the root domain of every service they bring in. A second badge counts the templates,
  in all eight languages.

  The sweep that measures this across the whole API used to carry a waiver naming this exact
  route. The waiver is gone and the assertion is now an equality, so the twelfth delete route
  written without a lookup is red the day it is written rather than the day somebody trusts
  its answer.

- **A notification webhook aimed at one provider survives that provider's deletion, keeps its
  green "enabled" badge, and never fires again.** A webhook's scope is two columns: a word
  (`all`, `provider`, `service`) and `webhooks.scope_ref_id`, a bare INTEGER holding the id
  the word points at. The column carries no REFERENCES clause, so no cascade reaches it, no
  `ON DELETE` blanks it, and no `PRAGMA foreign_key_list` sweep can see it — which matters
  because the census behind the deletion dialog was built from exactly that sweep, and the
  sweep was complete. It found all seven declared references to `providers.id`. The eighth
  was never in the answer.

  Measured end to end on a live instance: the 409 that refuses a provider deletion listed the
  services and the templates and did not mention the webhook; a provider whose only dependent
  was an alert rule raised no dialog at all and simply vanished; the row survived holding the
  dead id with `enabled = 1`; `_service_matches_scope` answered False for every service from
  then on; the journal held nothing about any of it; and the Webhooks tab drew the orphan as
  "Choose a provider" — the same words it draws for a rule nobody has configured yet. So the
  one screen that could still have told the operator, a week later, that their alerting had a
  hole in it said instead that the rule was merely unfinished. `delete_service` had the
  identical hole for `scope_type='service'`.

  Nothing is switched off or deleted by the fix, because either would be a second surprise:
  an operator who removes a provider and rebuilds it under a new id wants the alert rule
  waiting, not silently disarmed. What changes is that the state is now said at the three
  moments it can be said. `delete_provider` counts the webhooks scoped to the provider and
  refuses on them alone, and the dialog gives them their own paragraph, because none of the
  language written for services or templates is true of a webhook: nothing is published from
  one, so no hostname goes dark and there is no DNS record to withdraw — and the withdrawal
  checkbox is therefore not offered when webhooks are the whole conflict. It also says the
  sentence an operator would not guess, that a rule left switched on goes on looking armed.

  `delete_service` gets no such moment: its confirmation dialog is built in the browser from
  data already on the page, before any request is sent, so there is no 409 to add to. It
  writes the journal line after the fact instead, naming the webhooks it just orphaned — once
  per bulk delete rather than once per row, since ten rows selected in the table with a rule
  each are one thing that happened and ten warnings saying so bury it. And the Webhooks tab
  now labels such a scope "Provider deleted" or "Service deleted" with a "Matches nothing"
  badge, in all eight languages. That last one is the only one of the three an operator can
  still find later, which is why it is the one that had to be permanent rather than a toast.

  It is gated on the list query having actually answered. Before it does the list is empty,
  and an id matching nothing in an empty list is a provider nobody has fetched yet, not a
  provider that was deleted — without the gate every rule on the instance is accused of
  pointing at a corpse for the length of one page load, and a badge that cries on every load
  is not read on the load that counts.

  Two gates hold the class of defect rather than this instance of it, and both sit next to
  the FK sweep that missed it rather than inside it, since the sweep answers a question this
  column was never in. A column named `*_provider_id` holds a provider id whether or not
  anybody wrote REFERENCES beside it, so every such column must be read by the census before
  a provider is deleted. And every scope in `_WEBHOOK_SCOPE_TYPES` except `all` names a table
  whose rows can be deleted, so each must have a delete path that asks about it — a fourth
  scope fails the gate on the day it is added, not on the day somebody's alert stops
  arriving. Thirty-four tests cover the change, and eight negative controls confirm that each
  gate goes red when the line it guards is taken out.

- **"This will replace all current data" — for the action log and the uptime history it is a
  plain delete, and no backup file has ever carried either one.** A restore empties the same
  sixteen tables a reset does, then refills them from the file. Four of the sixteen are carried
  by no export on purpose, and two of those four are screens in the interface. So the instance
  restored onto loses its journal and its uptime history, the restore answers `ok: true`, and
  what is left on the Logs screen is one line about the restore itself — which reads like a
  quiet instance rather than an emptied one. Measured end to end on a live database: forty
  checks and twenty-five journal lines in, zero and one out.

  The same half-truth ran through three sentences. The hint above the Reset button offered the
  export as the undo — "Export a backup first: a reset cannot be undone." — and the export is
  the undo for the configuration and nothing else. The reset dialog named both tables but hung
  "that no backup carries" on the uptime history alone, so the log beside it read as covered.
  The restore dialog, the one that costs something, said neither: a reset is pressed by somebody
  who means to lose everything, a restore by somebody rolling back a bad change on an instance
  they intend to keep.

  All three now name the two tables and say that nothing brings them back, in all eight
  languages, and the restore dialog still lists the seven counts of what is coming in — the
  honesty is added, not bought by deleting the good news. Two gates hold it: the wording is
  bound to `_NOT_EXPORTED_ON_PURPOSE` in `backup.py`, so a table that stops being exempt fails
  the prose, and the deletion itself is measured against a running instance with a positive
  control that fails if the restore simply wiped everything.

- **Changing the admin password revoked every API key on the instance in nobody's mind but
  the operator's.** Measured against a running instance rather than read off the SQL: after a
  change, the browser that made it keeps its session, every other one is refused and has its
  cookie cleared, the old password opens nothing — and an admin-scoped API key minted before
  the change still answers 200 on every route. All four are deliberate. A key is a separate
  credential with its own lifetime, and expiring the monitoring dashboard's token because a
  human rotated their password would be a surprise of the other kind.

  The screen said "Update the admin password used to access Vauxtra." and nothing else, so
  the only way to learn any of it was to discover it. That matters in exactly one direction.
  Nobody changes an admin password idly; they change it because the old one may have leaked,
  and a password that leaked from a place where API keys also live has ended nothing at all
  while the keys are still valid. The form now says what the change ends — every other
  browser — and what it does not, naming how many keys are on this instance and linking to
  the tab that revokes them. The sentence about keys is counted and conditional: an install
  with none carries no warning about an empty list, which is how a warning stays worth
  reading. Pinned by tests on both ends, the backend one with a positive control so a suite
  where nothing was ever invalidated cannot pass it by accident.

- **Four sentences sent the operator to a screen that could not do what they promised.** The
  boot warning for a passwordless instance, and the same sentence in `docs/HOWTO.md`, both
  said to set a password in **Settings → API keys**; the password lives on **Settings →
  Security**, and the API keys tab has no field for it. `docs/HOWTO.md` also opened the
  templates walkthrough with **Settings → Templates**, a tab that has never existed —
  templates are a page of their own — and `README.md` named the form (**Change Password**)
  rather than the screen it sits on. Counted across the repository: eleven such paths
  describe Vauxtra's own interface, and four of them were wrong. No gate came with this. The
  two ends are prose and a React route, and the failure here is semantic rather than
  structural — "API keys" *is* a real tab, it simply cannot set a password — so a
  string-matching check would have passed the two that mattered most while tripping over
  every **Settings → API** that belongs to Pi-hole.

- **A provider that only a service template named was deleted with no question asked at all.**
  The schema declares seven references to `providers.id`. The delete path read four of them —
  the three on `services` and the one on `service_push_targets` — and the confirmation dialog
  was built out of those. The other three are `service_templates.proxy_provider_id`,
  `.dns_provider_id` and `.tunnel_provider_id`, and they carry `ON DELETE SET NULL` exactly as
  the ones on `services` do. So a provider no service pointed at fell straight through the
  check: no 409, no dialog, and every template that named it came back with an empty provider
  field nobody had emptied. With services also in the conflict, the dialog was meticulous about
  them and silent about the template it blanked in the same transaction.

  Templates get their own block rather than extra rows in the service list, because none of the
  service language is true of them. Nothing is published from a template, so no hostname goes
  dark, there is no record left live on the provider, and there is nothing to withdraw — the
  `?withdraw=true` checkbox is not offered when templates are the whole conflict. What a
  template loses is a choice somebody typed, and the dialog now says so before the deletion
  rather than leaving it to be found on the next service built from it. `?force=true` answers
  with `unlinked_templates`, and the journal gets its own line naming the templates that lost
  a provider, which is where an operator goes to find out why a template came back empty.

  Two things around it were wrong for the same reason. The dialog title was hard-coded to
  "Services still depend on it" at both call sites, which is simply untrue over a list of
  templates; one shared helper now answers it so the Integrations page and the Setup wizard
  cannot drift. And neither call site invalidated the Templates cache after a delete, so the
  page went on showing a provider that was no longer there.

  The guard is a gate rather than a list: `test_every_reference_the_schema_declares_is_asked_about`
  reads the foreign keys out of the live schema with `PRAGMA foreign_key_list` and holds them
  against the source of both readers. A fourth table given a provider column fails it the day it
  is written, not the day somebody deletes a provider.

- **The dialog that empties the database named seven of the eleven things it empties.** It
  listed services, providers, domains, tags, environments, webhooks and the log, and stopped.
  It did not name the templates the operator wrote by hand, the Docker endpoints they pointed
  at their hosts, the settings outside the five a reset protects, or the uptime history. That
  last one is the only casualty no backup carries, so the "export a backup first" line above
  the button could not have covered it, and the sentence beside it never said so. The message
  now names all eleven, and calls the uptime history out as the part no archive holds.

  It also never said what a reset keeps. The API keys minted on the instance are not in the
  wipe list and go on working afterwards, which is exactly what an operator resetting a box
  before handing it to somebody else needs to be told. The message now says the password and
  the API keys are kept.

  The room for those four nouns came from a sentence the field already carried:
  `ConfirmDialog` renders `ui.confirm.type_to_confirm` as the label of the input itself, so
  "Type RESET to confirm" inside the message was the second time the operator read it. The
  restore dialog, which asks for a word the same way, never repeated itself.

  `TheResetDialogNamesWhatItDestroysTests` reads `reset_all`'s own `DELETE` statements and
  holds every table it finds either to a word in the dialog or to a written reason it owes
  none — a join whose two ends are already named, or bookkeeping nobody typed. It also checks
  the survivors the message promises are really absent from the wipe list, and walks all eight
  locales for the repeated instruction. A table added to the reset from now on fails the suite
  until the sentence grows to cover it.

- **A backup carried every service and not one of the templates they were built from.** The
  restore empties sixteen tables and refills them from the archive; `service_templates` was in
  the wipe list and in neither export. An operator who reinstalled from a backup got their
  services, their providers, their domains and their tags back, an empty Templates page, and
  `ok: true` — the one table they fill in by hand was the one table no file carried. Both
  exports now include it, and the restore re-inserts it by explicit id like everything else, so
  `proxy_provider_id`, `dns_provider_id` and `tag_ids_json` still name the rows they named. The
  answer carries a `templates` count, which the panel shows beside the other six.

  The gap outlived a whole version because the guard held the wipe list against the *schema* and
  nothing held it against the *export*. `test_every_wiped_table_is_exported` now calls the
  export and compares its keys to the wipe list, so a table added to one has to be added to the
  other or named in `_NOT_EXPORTED_ON_PURPOSE` — which is where the four deliberate absences
  (the journal, the uptime stream, the webhook send queue, the scheduler's alert cursor) are now
  written down, with the reason they belong to the instance that produced the file rather than
  to the file.

  Restoring an archive written before this release still gives back no templates, because it
  holds none. That case now counts the rows before the wipe and writes a warning saying the
  table was emptied and the file carried none to put back, so it reads as the age of the file
  rather than as a bug. An instance that had no templates reads nothing.

  The Templates page also kept showing whatever it had cached before the restore:
  `RestoreSection.tsx` enumerates the caches a restore invalidates and `['templates']` was not
  among them.

- **The guided-setup step dots were buttons that told a screen reader they were list items.**
  Each dot jumps to its step, and each carried `role="listitem"` inside a `role="list"` strip.
  `listitem` is a structure role, so it replaces the button role rather than adding to it: the
  set was announced as a three-item list, and nothing said the items could be activated. An
  operator on a screen reader had no way to learn the dots were a shortcut. The dots keep their
  button role, and the strip carries `role="group"`, which names the set without making a claim
  about what its children are.

  It surfaced because `eslint-plugin-jsx-a11y` was never in the ESLint config, so no ARIA rule
  had ever run over the app. Its recommended set is now part of `npm run lint`, which the
  Frontend (Node) job already runs, so this is a gate rather than a one-time sweep, and the 34
  rules it turns on flag exactly this defect as an error. The other nineteen findings are
  deliberate patterns the rules cannot recognise from a single element: a combobox whose options
  are unfocusable because `aria-activedescendant` names them, a dialog backdrop whose keyboard
  path is Escape, a switch label beside a real `<button role="switch">` that a second tab stop
  would only make worse, and eight fields autofocused because the operator just opened the
  surface they sit on. Each now carries an inline waiver stating its reason, which is
  documentation those files did not have before.

- **The same number was grouped on one screen and bare on the next.** `formatNumber` puts the
  locale's thousands separator on a count, and half the app called it. Eight of the sixteen
  `StatCard` tiles passed their value through a formatter and eight handed over a raw number,
  so `certificates.expiring` read "12,480" on the dashboard and "12480" on the certificates
  page, and Monitoring's up/down/unknown tiles sat bare beside an availability that had gone
  through `formatPercent`. Six sentences had the same split inside one line: `t()` formats
  `{count}` and prints every other placeholder as it arrives, so the check summary read
  "45,000 services checked: 44987 up" and the services header read "50 routes shown of 1234".

  Every one of those numbers now goes through `formatNumber`, and the `{days}` on the
  certificates page joins the two dashboard call sites that already formatted it. The repair is
  per call site deliberately. Inside `t()` it would reach `{id}`, which carries a route id, and
  `{value}`, which carries a version string, and neither may ever take a separator. Inside
  `StatCard` it would put a `/settings` query in `components/ui/`, where no primitive fetches
  anything today.

  Only visible above 999, and above 9999 in Spanish, which does not group four digits.

- **Five confirmation dialogs kept their plural under a title that had counted to one.**
  `services.confirm.bulk_delete_title` inflects — "Delete 1 route?" — and the body beneath it
  read "Their proxy hosts and DNS records are removed from the providers." The same mismatch
  sat under the enable, disable and check dialogs, and on the expose wizard's preflight, where
  "1 blocking failure" was followed by "Fix them in the previous step." The bodies were plain
  keys, so `t()` had one sentence to give whatever the number was, and four of the five call
  sites were already handing it a `{ count }` it had nowhere to put.

  Each body is now a counted sentence carrying the forms its own language declares, and
  `ExposeModal.tsx` passes the count it was already using for the title just above. Japanese
  and Chinese declare a single plural category, so their one form has to hold at every number:
  two Chinese strings said "them" outright and are reworded, and "probed one after the other"
  is dropped in both, since at one route there is no sequence to describe.

  Portuguese carried a second fault of its own in the same place. `expose.preflight.blocked_body`
  answered "Corrija-os" to a title counting "falhas bloqueantes", which is feminine, so the
  plural is corrected alongside the singular it never had.

- **The API tells "a provider refused" apart from "Vauxtra broke", and the panel threw that
  distinction away.** `app/api/providers.py`, `app/api/docker.py` and `app/api/webhooks.py`
  raise `502` in eleven places between them, and each argues the choice in its own comment:
  the request left, the far end refused, and nothing in this server broke. The panel mapped
  every status at or above 500 onto one sentence — "The server ran into a problem. Try again
  in a moment." — so an expired Cloudflare token, a Docker socket that was never mounted and
  a webhook target that refused all read as a fault in Vauxtra, sending an operator to read
  its logs when the answer was on their own side.

  `common.error.upstream` — "The service Vauxtra contacted refused the request. Check that
  integration, then try again." — was already written and already translated into all eight
  locales. Nothing referenced it. `translateApiError` now answers it for a 502, but only when
  FastAPI wrote the body: a 502 from a proxy in front of the API means the API itself never
  answered, and that one really is this server. `frontend/src/lib/errors.test.ts` is new and
  pins both halves, along with the rest of the status table, which had no test at all.

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
  refetches it in the background on every remount while it is stale and whenever anything
  invalidates it. The error branch came first in the render, so a refetch that failed swapped
  the card — set a password, change it, or the environment-managed notice — for a full-width
  alert, and the next fetch that succeeded mounted a fresh, empty one. React Query keeps the
  last good data through a failed background refetch, so nothing was ever actually unknown:
  the form was discarded over an answer the panel already had. A blip behind a reverse proxy
  was enough, and the operator did nothing to cause it.

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

- **One rule about automatic DNS was written twice, and the two copies already disagreed.**
  "An automatic public target needs a DNS provider that can resolve one" decided both what the
  expose form drew and what the payload sent, and it was written once for each. The copies
  differed by a single clause: the payload asked whether the selected provider had been found
  at all, the form did not. So for a `dns_provider_id` naming a provider the catalogue could
  not resolve — deleted, or simply not answered for yet — the form computed `manual` with the
  automatic update off while the payload sent `auto` with it on.

  Nothing ever showed it, and that is the only reason this is not filed as a bug. The form's
  value has exactly one consumer, the automatic-update switch, and that switch is drawn only
  for a provider that resolves — which is every input where the two copies agreed. The
  disagreement lived precisely where nobody could see it, and would have become visible the
  day someone widened that condition.

  `autoPublicTarget()` in `components/features/expose/types.ts` now answers the question once:
  the mode, the update flag, and whether the switch has anything true to say at all. The form
  calls it, the payload calls it, and so does the handler that reacts to a provider change, so
  what is drawn, what is sent and what is written back into the form state are one computation.
  The reading that survived is the payload's: a provider that cannot be resolved has said
  nothing, and reading nothing as "cannot" is what dropped an operator's automatic update the
  last time it happened — the same lesson `CAPABILITY_FALLBACK` in `lib/providers.ts` carries.

  `components/features/expose/types.test.ts` pins the matrix, including the two rows the copies
  answered differently and the catalogue-down row that must not drop a saved setting. It also
  reads both components as text, so a second writer of the rule fails the build instead of
  waiting to be noticed in review, and four mutations were used to prove each half can fail.

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

- **Thirteen locale keys that no code path could reach, in eight languages each.** A key
  nothing reads is eight translations of nothing, and it invites the next reader to believe a
  feature is there. Four were labels for settings tabs that no longer exist: `tags`,
  `environments`, `migration` and `backup` were folded into `taxonomy` and `data` and survive
  only as `?tab=` values in `TAB_ALIASES`, which redirect and never render a label.
  `settings.group.help` named a sidebar group absent from `SETTINGS_GROUPS`.
  `settings.logs.clearing` and `settings.migration.importing` predate `Button`'s `loading`
  prop, which shows a spinner and sets `aria-busy` under the button's own label.
  `dashboard.quick_actions.shortcuts` spelled out "G then D, P, S" as prose before the card
  rendered real `<Kbd>` chips. `monitoring.table.provider` and `monitoring.table.target` were
  headers for two columns the table never grew, and the drawer shows both under other keys.
  `settings.list.compact_rows` and `settings.language.contribute_hint` named a row-density
  toggle and a fourth contribution hint that were never built.

  Neither locale gate could have found them: `check-locale-parity.mjs` and
  `check-locale-quality.mjs` read `src/locales/*.json` and never open a component, so parity
  between the keys and the code is unchecked in both directions. The direction that would
  print a raw key name to an operator was measured and is clean — every key a `t()` call
  names exists in `en.json`, including those assembled in two stages, as the provider wizard
  and the taxonomy tab both do.

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
