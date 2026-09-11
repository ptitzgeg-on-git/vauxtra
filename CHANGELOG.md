# Changelog

All notable changes to this project will be documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) — versioning follows [SemVer](https://semver.org/).

---

## [Unreleased]

### Security

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

### Fixed

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
[1.0.2]: https://github.com/ptitzgeg-on-git/vauxtra/releases/tag/v1.0.2
[1.0.1]: https://github.com/ptitzgeg-on-git/vauxtra/releases/tag/v1.0.1
[1.0.0]: https://github.com/ptitzgeg-on-git/vauxtra/releases
[0.1.0]: https://github.com/ptitzgeg-on-git/vauxtra/releases/tag/v0.1.0
