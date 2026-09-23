"""What the gate read out of the panel, read again by hand.

`scripts/check_panel_contract.py` compares every `api.post`, `api.put` and `api.patch` body
in the panel against the Pydantic model of the route it posts to. It has to resolve those
bodies out of TypeScript with regexes and bracket counting -- it cannot run `tsc`, and the CI
job that runs it installs no Node at all -- so the question underneath every verdict it gives
is whether what it read is what the file says.

This file answers that in both directions. The key sets below were read out of the panel by
hand and written here; the test fails if the resolver stops agreeing with them, and it fails
if somebody edits one of those call sites without editing the expectation. A gate whose
reading nobody checks is a gate that can be green for the wrong reason.

The premise is pinned too. Seventeen of the twenty-one models the panel sends a body to do
not set `extra="forbid"`, so an undeclared key is dropped in silence and the call succeeds
all the same; only `ServiceIn`, `ServiceLabelsIn`, `ServicePreflightIn` and `TemplateIn`
refuse it. `UnknownKeysAreDroppedNotRefused` sends both kinds over HTTP, because if every
model refused, this gate would have nothing left to catch and should be deleted rather than
kept green.
"""

import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import app.auth as auth
import app.db as db
import app.main as app_main
import app.scheduler as scheduler
from app import models

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_panel_gate():
    """`scripts/` is not a package, so the gate is loaded by path.

    It goes into `sys.modules` before it runs. The gate uses `from __future__ import
    annotations`, which leaves every annotation a string, and `@dataclass` resolves those by
    looking its own module up by name -- a module loaded by path and never registered is not
    there, and the class raises on definition rather than on use.
    """
    path = REPO_ROOT / "scripts" / "check_panel_contract.py"
    spec = importlib.util.spec_from_file_location("check_panel_contract", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("check_panel_contract", module)
    spec.loader.exec_module(module)
    return sys.modules["check_panel_contract"]


# Read by hand out of the panel, one call per way a body can be written. Keyed the way the
# gate keys a call -- file, verb, URL expression exactly as it appears -- so a call that moves
# down its file does not need touching here. The values are sorted tuples because that is
# what the resolver answers with; the order carries no meaning.
GROUND_TRUTH = {
    # An object literal written at the call site.
    (
        "components/features/settings/ApiKeysTab.tsx", "POST", "'/settings/api-keys'",
    ): ("name", "scopes"),

    # An identifier annotated in the parameter list of the arrow that contains the call.
    (
        "components/features/monitoring/AlertsEditor.tsx",
        "POST",
        "`/services/${serviceId}/alerts`",
    ): ("alerts",),

    # A call, resolved across files through the callee's single `return`. `buildPayload` is
    # declared in two files and this one imports the provider flavour.
    (
        "hooks/useProviderMutations.ts", "POST", "'/providers'",
    ): ("extra", "name", "password", "type", "url", "username"),

    # The same call inside a spread, with one more key beside it.
    (
        "hooks/useProviderMutations.ts", "POST", "'/providers/validate-draft'",
    ): ("extra", "name", "password", "type", "url", "username", "write_probe"),

    # A name destructured out of an annotated parameter, whose type is a member of an
    # interface declared in another file.
    (
        "hooks/useProviderMutations.ts", "PUT", "`/providers/${id}`",
    ): ("enabled", "extra", "name", "password", "url", "username"),

    # A rest element: every member of the annotation except the ones named beside it.
    (
        "components/features/settings/TaxonomyTab.tsx", "PUT", "`${cfg.endpoint}/${id}`",
    ): ("color", "name"),

    # Typed only by the third generic argument of the `useMutation` the `mutationFn` is in.
    (
        "components/features/templates/TemplateModal.tsx", "POST", "'/templates'",
    ): (
        "description", "dns_ip", "dns_provider_id", "domain", "environment_ids",
        "expose_mode", "forward_scheme", "icon_url", "name", "proxy_provider_id",
        "public_target_mode", "tag_ids", "target_port", "tunnel_provider_id", "websocket",
    ),

    # A call whose result is read from its declared return type rather than its body.
    (
        "pages/Services.tsx", "PUT", "`/services/${service.id}`",
    ): (
        "auto_update_dns", "dns_ip", "dns_provider_id", "domain", "enabled",
        "environment_ids", "expose_mode", "extra_dns_provider_ids", "extra_proxy_provider_ids",
        "forward_scheme", "icon_url", "proxy_provider_id", "public_target_mode", "subdomain",
        "tag_ids", "target_ip", "target_port", "tunnel_hostname", "tunnel_provider_id",
        "websocket",
    ),

    # The panel's only PATCH, the label edit: an identifier typed by the parameter of its
    # `mutationFn`, through an interface declared in another file whose members are optional.
    (
        "components/features/expose/ExposeModal.tsx", "PATCH", "`/services/${serviceId}`",
    ): ("environment_ids", "icon_url", "tag_ids"),
}


class _PanelCase(unittest.TestCase):
    """The gate loaded once, with its calls indexed by the key it gives them."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.gate = _load_panel_gate()
        cls.index = cls.gate.PanelIndex(REPO_ROOT)
        cls.calls = cls.gate.collect_panel_calls(cls.index)
        cls.by_key = {c.key: c for c in cls.calls}


class TheResolverAnswersWithWhatTheFileSays(_PanelCase):
    """Nine call sites, read by hand first: eight ways of writing a body, and the one PATCH."""

    def test_every_hand_read_call_resolves_to_exactly_those_keys(self) -> None:
        for key, expected in GROUND_TRUTH.items():
            with self.subTest(call=key):
                call = self.by_key.get(key)
                self.assertIsNotNone(call, f"{key} is no longer a call the gate finds")
                self.assertEqual(call.keys, expected)

    def test_each_of_those_calls_is_one_the_gate_actually_compares(self) -> None:
        """Resolving a body and never comparing it would be green and worthless.

        One of the nine is read and still not compared, and the reason is worth keeping
        separate: `TaxonomyTab` posts to `cfg.endpoint`, one form driving `/api/tags` and
        `/api/environments`, so the body is legible and the route is not. The resolver did
        its half; there are simply two models it could be held to. That is a different
        failure from a body nobody could read, and the assertion says which one it is.
        """
        _calls, _findings, uncompared, _stats = self.gate.run(REPO_ROOT)
        why_by_key = {c.key: why for c, why in uncompared}
        for key in GROUND_TRUTH:
            with self.subTest(call=key):
                if key[2].startswith(("'", '"')) or "cfg." not in key[2]:
                    self.assertNotIn(key, why_by_key)
                else:
                    self.assertEqual(why_by_key.get(key), "route not a literal")


class NamesBindTheWayModulesBindThem(_PanelCase):
    """Seventeen function names and six type names are declared in more than one file."""

    def test_a_function_declared_twice_resolves_to_the_one_this_file_imports(self) -> None:
        homes = {s.rel for s in self.index.fns_by_name["buildPayload"]}
        self.assertEqual(
            homes,
            {
                "components/features/expose/ExposeModal.tsx",
                "components/features/providers/providerConstants.ts",
            },
            "the two buildPayloads this test is about have moved",
        )
        hook = self.index.by_stem["hooks/useProviderMutations"]
        found = self.index.lookup_fn(hook, "buildPayload")
        self.assertIsNotNone(found)
        self.assertEqual(found[0].rel, "components/features/providers/providerConstants.ts")

    def test_the_two_of_them_return_different_things_so_a_union_would_be_wrong(self) -> None:
        """A flat repo-wide table would answer with the keys of both, which is neither.

        `ExposeModal`'s local one builds a `ServicePayload`; the exported one builds a
        provider. There is no key in common, so unioning them would have turned one clean
        call into twenty invented divergences.
        """
        expose = self.index.by_stem["components/features/expose/ExposeModal"]
        constants = self.index.by_stem["components/features/providers/providerConstants"]
        theirs = self.index.call_keys(expose, "buildPayload", 0)
        ours = self.index.call_keys(constants, "buildPayload", 0)
        self.assertIsNotNone(theirs)
        self.assertIsNotNone(ours)
        self.assertEqual(theirs & ours, set())

    def test_a_type_declared_twice_resolves_to_the_one_in_scope(self) -> None:
        """`StatusFilter` means two disjoint things, and two pages import one each.

        `monitoring/uptime.ts` declares it `MonitoringStatus | 'all'`; `services/helpers.ts`
        declares it `'ok' | 'error'`. No string is in both. `pages/Monitoring.tsx` and
        `pages/Services.tsx` each import the name from the file they mean, so the resolver has
        to answer differently for the two of them from the same identifier -- which is the
        half a flat repo-wide table gets wrong.
        """
        homes = {s.rel for s in self.index.types_by_name["StatusFilter"]}
        self.assertEqual(
            homes,
            {
                "components/features/monitoring/uptime.ts",
                "components/features/services/helpers.ts",
            },
            "the two StatusFilters this test is about have moved",
        )
        for stem, home in (
            #: the declaring file itself: its own declaration wins over the other one
            ("components/features/monitoring/uptime", "components/features/monitoring/uptime.ts"),
            ("components/features/services/helpers", "components/features/services/helpers.ts"),
            #: a consumer that declares neither: the import picks which of the two it gets
            ("pages/Monitoring", "components/features/monitoring/uptime.ts"),
            ("pages/Services", "components/features/services/helpers.ts"),
        ):
            with self.subTest(module=stem):
                found = self.index.lookup_type(self.index.by_stem[stem], "StatusFilter")
                self.assertIsNotNone(found)
                self.assertEqual(found[0].rel, home)

    def test_a_name_re_exported_rather_than_declared_is_followed_to_its_home(self) -> None:
        """A module can bind a name without declaring it, and two in the panel do.

        `hooks/useDockerDiscovery.ts` imports `DockerContainer` from `types/api.ts` and
        re-exports it, so `DockerSection.tsx` can keep importing the type from the hook it
        already imports the query from. `components/features/provider-modal/index.ts` is the
        other form: a barrel that names `WizardMode` in an `export { ... } from './...'` it
        does not declare either. A resolver that stops at the first hop reads both of those
        modules as declaring nothing and refuses a name the panel resolves fine -- and it is
        deleting a panel's private copy of a shared type, the fix, that creates them.
        """
        for stem, name, home in (
            ("components/features/settings/data/DockerSection", "DockerContainer",
             "types/api.ts"),
            ("components/features/settings/data/DockerSection", "DockerEndpoint",
             "types/api.ts"),
            ("components/features/ProviderModal", "WizardMode",
             "components/features/provider-modal/StepCredentials.tsx"),
        ):
            with self.subTest(module=stem, name=name):
                hop = self.index.by_stem[stem]
                self.assertNotIn(name, hop.types, f"{stem} declares {name} itself now")
                found = self.index.lookup_type(hop, name)
                self.assertIsNotNone(found)
                self.assertEqual(found[0].rel, home)

    def test_a_re_export_cycle_stops_instead_of_recursing_forever(self) -> None:
        """Two modules each sending the name to the other is a hang, not a wrong answer.

        Nothing in the panel does this today, so the witness is built here rather than found:
        following a chain is only safe because the walk refuses to visit a module twice.
        """
        loop = {}
        for rel, other in (("__a.ts", "@/__b"), ("__b.ts", "@/__a")):
            src = self.gate.Source(rel=rel, raw="", code="")
            src.imports["Ghost"] = other
            loop[rel] = src
            self.index.by_stem[src.stem] = src
            self.addCleanup(self.index.by_stem.pop, src.stem, None)
        self.assertIsNone(self.index.lookup_type(loop["__a.ts"], "Ghost"))

    def test_a_name_neither_declared_here_nor_imported_is_refused(self) -> None:
        """Three files declare `RouteModal` and none of them imports it from elsewhere."""
        self.assertGreater(len(self.index.types_by_name["RouteModal"]), 1)
        stranger = self.index.by_stem["hooks/useProviderMutations"]
        self.assertIsNone(self.index.lookup_type(stranger, "RouteModal"))


class TheResolverRefusesRatherThanGuesses(_PanelCase):
    """The regression that decided the design: a wrong key set is worse than none."""

    def test_an_annotation_outside_the_enclosing_function_is_not_read_as_the_body(self) -> None:
        """`updateProvider` has an `onSuccess: (data: ProviderValidationResult)` beside it.

        An earlier draft scanned backwards for the nearest `name:` and found that one twenty
        lines up, reporting the provider update as sending `ok`, `health` and `validation`.
        Those three keys are what this asserts is absent: not that the call resolves, which
        the ground truth above already covers, but that it does not resolve to the neighbour.
        """
        call = self.by_key[("hooks/useProviderMutations.ts", "PUT", "`/providers/${id}`")]
        self.assertNotIn("validation", call.keys)
        self.assertNotIn("health", call.keys)
        self.assertNotIn("ok", call.keys)

    def test_a_body_the_resolver_cannot_bind_is_uncompared_and_not_empty(self) -> None:
        """`unknown` is not "no keys"; treating it as one would compare against nothing."""
        sync = (
            "components/features/settings/data/SyncSection.tsx", "POST", "'/services/import'",
        )
        self.assertIsNone(self.by_key[sync].keys)

    def test_every_call_the_gate_will_not_compare_says_why_in_one_place(self) -> None:
        _calls, _findings, uncompared, _stats = self.gate.run(REPO_ROOT)
        for call, _why in uncompared:
            with self.subTest(call=call.key):
                self.assertIn(call.key, self.gate.ALLOWED_UNCOMPARED)
                self.assertTrue(self.gate.ALLOWED_UNCOMPARED[call.key].strip())


class TheGateComparesEnoughToBeWorthFailingABuild(_PanelCase):
    """A gate that quietly compares nothing passes just as green as one that works."""

    def test_no_panel_call_sends_a_key_outside_the_commented_exemptions(self) -> None:
        _calls, findings, _uncompared, _stats = self.gate.run(REPO_ROOT)
        divergent = [m for k, m in findings if k not in self.gate.ALLOWED_PANEL_KEYS]
        self.assertEqual(divergent, [])

    def test_no_allowance_outlives_the_call_it_was_written_for(self) -> None:
        _calls, findings, uncompared, _stats = self.gate.run(REPO_ROOT)
        observed = {c.key for c, _why in uncompared}
        self.assertEqual(sorted(set(self.gate.ALLOWED_UNCOMPARED) - observed), [])
        self.assertEqual(
            sorted(set(self.gate.ALLOWED_PANEL_KEYS) - {k for k, _m in findings}), []
        )

    def test_most_of_what_the_panel_sends_is_actually_compared(self) -> None:
        _calls, _findings, _uncompared, stats = self.gate.run(REPO_ROOT)
        self.assertGreaterEqual(stats["compared"], 28)
        self.assertGreaterEqual(stats["compared"], stats["bodies"] - 8)


class UnknownKeysAreDroppedNotRefused(unittest.TestCase):
    """Why this gate exists: the app on its own database, answering a key nobody declared."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._orig = (models.DB_PATH, models.DATA_DIR, db.DB_PATH, db.DATA_DIR)

        models.DATA_DIR = db.DATA_DIR = self._tmpdir.name
        models.DB_PATH = db.DB_PATH = os.path.join(self._tmpdir.name, "panel.test.db")
        models.init_db()

        self._patches = [
            patch.object(auth, "APP_PASSWORD", ""),
            patch.object(scheduler, "start", lambda interval_minutes=0: None),
            patch.object(scheduler, "configure", lambda interval_minutes=0: None),
        ]
        for p in self._patches:
            p.start()
        self._client_cm = TestClient(app_main.app, raise_server_exceptions=False)
        self.client = self._client_cm.__enter__()

    def tearDown(self) -> None:
        self._client_cm.__exit__(None, None, None)
        for p in reversed(self._patches):
            p.stop()
        models.DB_PATH, models.DATA_DIR, db.DB_PATH, db.DATA_DIR = self._orig
        self._tmpdir.cleanup()

    def test_a_key_the_model_does_not_declare_is_accepted_and_lost(self) -> None:
        """`201`, no mention of the field, and nothing stored. This is the whole problem."""
        resp = self.client.post(
            "/api/webhooks",
            json={
                "name": "on-call",
                "url": "json://hook.test/x",
                "notify_on_recovery": True,   # no such field, in any version
            },
        )
        self.assertEqual(resp.status_code, 201, resp.text)
        self.assertNotIn("notify_on_recovery", resp.json())

    def test_a_model_that_forbids_it_answers_422_instead(self) -> None:
        """Where `extra="forbid"` is set, pydantic says so and no gate is needed."""
        resp = self.client.post(
            "/api/services",
            json={
                "subdomain": "app", "domain": "example.com",
                "target_ip": "10.0.0.5", "target_port": 8080,
                "notify_on_recovery": True,
            },
        )
        self.assertEqual(resp.status_code, 422, resp.text)

    def test_most_models_the_panel_posts_to_take_the_silent_half(self) -> None:
        """If they all refused, this gate would be redundant and should be deleted."""
        gate = _load_panel_gate()
        panel_models, routes = gate.collect_api_contracts(REPO_ROOT)
        by_path = {}
        for (method, path), route in routes.items():
            by_path.setdefault((method.upper(), gate.normalise(path)), route)

        silent, refusing = set(), set()
        for call in gate.collect_panel_calls(gate.PanelIndex(REPO_ROOT)):
            if not call.path or call.keys is None:
                continue
            route = by_path.get((call.method, gate.normalise(call.path)))
            if route is None or not route.model:
                continue
            model = panel_models.get(route.model)
            if model is None:
                continue
            (refusing if model.forbids_extra else silent).add(route.model)

        self.assertGreaterEqual(len(silent), 10)
        self.assertGreater(len(silent), len(refusing))


if __name__ == "__main__":
    unittest.main(verbosity=2)
