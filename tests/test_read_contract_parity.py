"""What the read-side gate resolved, resolved again by hand.

`scripts/check_read_contract.py` compares the declared type of every GET the panel makes
against the other declarations of the same bytes. Nothing validates those declarations at
runtime -- there is no `response_model=` in `app/`, and `api.get<T>` ends in a cast -- so
the gate is the only thing that ever contradicts one, and a gate nobody checks is a gate
that can be green for the wrong reason.

So the shapes below were read out of `frontend/src/types/api.ts` by hand and written here.
The test fails if the resolver stops agreeing with them, and it fails if somebody edits one
of those declarations without editing the expectation.

The witnesses are picked for what each one exercises rather than for coverage. `Certificate`
is the shape the gate was written from -- it is the one whose second declaration let the
certificates table render a chip no provider has ever filled. `Template` is `Omit<TemplateIn,
'websocket'>` with a `websocket` of its own put back, which is the only construct in the
panel where expanding a utility type and then applying the interface's own members can give
a different answer from doing either alone. `Pick<AuthStatus, 'auth_mode'>` is the narrowing
the gate has to allow rather than fail. And `ProvidersHealthMap` is the shape it has to
refuse: an alias for an index signature, which declares no key and can contradict nothing.
"""

import importlib.util
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load(name: str):
    """`scripts/` is not a package, so the gates are loaded by path.

    Each goes into `sys.modules` before it runs. They use `from __future__ import
    annotations`, which leaves every annotation a string, and `@dataclass` resolves those by
    looking its own module up by name -- a module loaded by path and never registered is not
    there, and the class raises on definition rather than on use.
    """
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(name, module)
    spec.loader.exec_module(module)
    return sys.modules[name]


class _ReadCase(unittest.TestCase):
    """The gate loaded once, with the panel indexed once behind it."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.panel = _load("check_panel_contract")
        cls.gate = _load("check_read_contract")
        cls.index = cls.gate.PanelIndex(REPO_ROOT)
        cls.api = cls.index.by_stem["types/api"]

    def shape_of(self, expr: str, stem: str = "types/api"):
        return self.gate.shape(self.index, self.index.by_stem[stem], expr)

    def keys_of(self, expr: str, stem: str = "types/api"):
        """A resolved object as (required, optional), both sorted."""
        got = self.shape_of(expr, stem)
        self.assertIsNotNone(got, f"{expr} did not resolve")
        self.assertEqual(got[0], "object", f"{expr} resolved to {got[0]}")
        return (
            sorted(k for k, (opt, _) in got[1].items() if not opt),
            sorted(k for k, (opt, _) in got[1].items() if opt),
        )


class TheResolverAnswersWithWhatTheFileSays(_ReadCase):
    """Four declarations, read by hand first, one per construct the resolver has to handle."""

    def test_the_certificate_row_carries_the_keys_both_routes_actually_send(self) -> None:
        """Seven keys on every row of both routes, three that only Zoraxy fills.

        `GET /api/certificates` spreads whatever the provider's `get_certificates()` built
        and adds `provider_id`, `provider_name` and `domain_names` on top, so those seven
        are on every row. `remaining_days`, `use_dns` and `is_fallback` come from Zoraxy
        alone; NPM rows carry none of the three.
        """
        self.assertEqual(
            self.keys_of("Certificate"),
            (
                [
                    "domain_names",
                    "domains",
                    "expires_on",
                    "id",
                    "nice_name",
                    "provider_id",
                    "provider_name",
                ],
                ["is_fallback", "remaining_days", "use_dns"],
            ),
        )

    def test_the_four_keys_the_expiry_route_adds_are_the_only_optional_ones(self) -> None:
        """`CertificateRow` is the same row from whichever of the two routes answered.

        `/expiry` is the page's source of truth and `/certificates` is the fallback it
        falls back to, so exactly the four keys `/expiry` computes are optional on top of
        the base. This used to be declared the other way round -- every key optional, plus
        an `issuer` and a `provider` no provider sends -- and the table rendered a chip off
        the `issuer` that could never appear.
        """
        required, optional = self.keys_of("CertificateRow")
        base_required, base_optional = self.keys_of("Certificate")
        self.assertEqual(required, base_required)
        self.assertEqual(
            sorted(set(optional) - set(base_optional)),
            ["days_remaining", "expired", "expiring_soon", "expiry_date_raw"],
        )
        for gone in ("issuer", "provider"):
            self.assertNotIn(gone, required + optional, f"{gone} is back and nothing sends it")

    def test_a_type_that_omits_a_key_and_declares_its_own_keeps_its_own(self) -> None:
        """`Template extends Omit<TemplateIn, 'websocket'>` and then declares `websocket`.

        `TemplateIn.websocket` is `boolean`, because that is what the form posts.
        `Template.websocket` is `boolean | number`, because SQLite gives the column back as
        `0` or `1`. A resolver that expanded the `Omit` and stopped would answer `boolean`
        for a row that arrives as a number; one that skipped the `Omit` and read only the
        parent would answer the same. Getting this right needs both halves, in order.
        """
        parent_required, parent_optional = self.keys_of("TemplateIn")
        required, optional = self.keys_of("Template")
        self.assertEqual(optional, [], "nothing in Template is optional today")
        self.assertEqual(parent_optional, [], "nothing in TemplateIn is optional today")
        self.assertEqual(
            sorted(set(parent_required) | {"id", "created_at"}),
            required,
            "Template is TemplateIn plus the two keys the database adds",
        )
        got = self.shape_of("Template")
        self.assertEqual(got[1]["websocket"][1], ("opaque", "boolean | number"))
        parent = self.shape_of("TemplateIn")
        self.assertEqual(parent[1]["websocket"][1], ("prim", "boolean"))

    def test_a_pick_resolves_to_the_one_key_it_names(self) -> None:
        """`Layout.tsx` reads `/auth/status` for one field out of five, and says so.

        Refusing to read a `Pick` would have meant writing an exemption for the four places
        that read this route, three of which state the whole thing and agree about it.
        """
        self.assertEqual(
            self.keys_of("Pick<AuthStatus, 'auth_mode'>", "components/layout/Layout"),
            ([], ["auth_mode"]),
        )
        required, optional = self.keys_of("AuthStatus")
        self.assertIn("auth_mode", optional)
        self.assertEqual(required, ["auth_required", "authenticated", "setup_required"])

    def test_an_index_signature_resolves_to_nothing_rather_than_to_no_keys(self) -> None:
        """`Record<string, T>` declares no key, and an empty key set is not the same claim.

        `ProvidersHealthMap` is `Record<string, ProviderHealthSummary>`. Read as an object
        with no members, it would contradict every reader that names a key; read as
        unreadable, it contradicts nobody, which is the truth about an index signature.
        `unknown` is the same answer reached the other way, and the Dashboard uses it on
        purpose because it only counts the entries that came back.
        """
        self.assertIsNone(self.shape_of("ProvidersHealthMap"))
        self.assertEqual(self.shape_of("Record<string, string>"), ("opaque", "Record<string, string>"))
        self.assertEqual(self.shape_of("unknown"), ("opaque", "unknown"))
        self.assertEqual(self.shape_of("string[]"), ("array", ("prim", "string")))


class ReadersAreGroupedByTheBytesTheyRead(_ReadCase):
    """Two files reading one endpoint, and two files sharing one cache entry."""

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.groups = cls.gate.collect(cls.index)

    def test_a_path_parameter_does_not_split_one_route_into_many(self) -> None:
        """`/providers/${id}/health` and `/providers/${pid}/health` are one route.

        Which variable holds the id does not change which handler answers, so the group is
        keyed on the route with the parameters written out. The query string goes for the
        same reason: `/docker/containers?all=1` is the same handler as `/docker/containers`.
        """
        for name in (
            "route:/providers/*/health",
            "route:/providers/*/dns-records",
            "route:/services/*/alerts",
            "route:/docker/containers*",
        ):
            with self.subTest(route=name):
                self.assertIn(name, self.groups)

    def test_a_cache_key_that_starts_with_a_variable_groups_with_nothing(self) -> None:
        """`[kind]` in one panel is not `[other]` in another, however alike they look.

        `TaxonomyTab.tsx` is the one site in the panel whose `queryKey` is a bare variable.
        Normalising it to `[*]` and grouping on that would put it with any other such key
        and invent a disagreement between two panels that never touch the same entry.
        """
        self.assertNotIn("key:[*]", self.groups)
        self.assertIn("key:['provider-health', *]", self.groups)

    def test_the_two_groupings_catch_different_things(self) -> None:
        """Four readers of `/auth/status`, and four of `['auth-status']` -- not the same four.

        `App.tsx` and `Sidebar.tsx` read the route inside a query on that key, so they land
        in both. `pages/Login.tsx` reads the route with no query behind it at all, and
        `Layout.tsx` reads the cache entry without a `queryFn` of its own. Either grouping
        alone would miss one of the two.
        """
        route = {o.src.rel for o in self.groups["route:/auth/me"]}
        key = {o.src.rel for o in self.groups["key:['auth-status']"]}
        self.assertTrue(route - key, "every reader of the route is also on the key")
        self.assertTrue(key - route, "every reader of the key also names the route")


class TwoAnswersAboutOneByteStreamAreCaught(_ReadCase):
    """The comparison, run against shapes written here so it has something to fail on.

    Nothing in the panel disagrees with itself today, which is the point of the gate and
    also the reason this class exists: a comparison with nothing left to catch passes just
    as green as one that never worked.
    """

    def compare(self, left: str, right: str):
        a, b = self.shape_of(left), self.shape_of(right)
        self.assertTrue(self.gate.comparable(a, b), f"{left} vs {right} was skipped")
        return self.gate.compare(a, b)

    def test_the_same_key_declared_two_ways_is_a_contradiction(self) -> None:
        """`expires_on` is on every row of both routes, and `""` when the date would not parse.

        The panel used to say both things about it: one declaration had it required, the
        other had every key optional. At most one of two answers about the same bytes is
        right, and which one is not decidable from the panel -- so the gate refuses the
        pair rather than picking.
        """
        conflicts, narrowed = self.compare("{ expires_on: string }", "{ expires_on?: string }")
        self.assertEqual(narrowed, [])
        self.assertEqual(conflicts, ["expires_on: optional on one side, required on the other"])
        conflicts, _ = self.compare("{ id: number }", "{ id: string }")
        self.assertEqual(conflicts, ["id: number on one side, string on the other"])

    def test_a_disagreement_under_an_array_is_still_a_disagreement(self) -> None:
        """Both routes answer a list, so every comparison that matters is under a `[]`."""
        conflicts, _ = self.compare("{ id: number }[]", "{ id: string }[]")
        self.assertEqual(conflicts, ["[].id: number on one side, string on the other"])

    def test_reading_fewer_keys_than_arrive_is_not_a_contradiction(self) -> None:
        """`pages/Setup.tsx` reads `/providers` as `ProviderItem[]`, and that is allowed.

        Eight of `Provider`'s keys are absent from it. None of the eight is a disagreement:
        a caller that reads four fields out of twelve is not claiming the other eight are
        not there. It is counted rather than failed, so a narrowing that grows is visible
        without a build that fails for it.
        """
        conflicts, narrowed = self.compare("{ a: string, b: number }", "{ a: string }")
        self.assertEqual(conflicts, [])
        self.assertEqual(narrowed, ["b"])

    def test_a_shape_that_declares_no_key_contradicts_nothing(self) -> None:
        """An index signature and `unknown` are claims about nothing, not claims of nothing."""
        for left, right in (
            ("{ a: string }", "Record<string, string>"),
            ("unknown", "Record<string, string>"),
            ("ProvidersHealthMap", "unknown"),
        ):
            with self.subTest(left=left, right=right):
                self.assertFalse(
                    self.gate.comparable(self.shape_of(left), self.shape_of(right))
                )
        #: Two readers writing the same opaque expression agree about it either way, and
        #: asking for a written excuse to compare a declaration with itself is how an
        #: exemption table fills up with entries nobody rereads.
        self.assertTrue(
            self.gate.comparable(
                self.shape_of("Record<string, string>"), self.shape_of("Record<string, string>")
            )
        )


class OneDeclarationPerShape(_ReadCase):
    """`types/api.ts` owns the shapes more than one panel reads, and owns them alone."""

    def test_no_panel_module_declares_a_name_the_shared_file_declares(self) -> None:
        self.assertEqual(self.gate.shadowed(self.index), [])

    def test_the_rule_would_notice_if_one_did(self) -> None:
        """Seven declarations were deleted to get this to zero; it has to stay a real check.

        Every one of them was the stale copy -- a `DockerContainer` with a `ports` array no
        handler sends, a `ContainerSuggestion` for a route that no longer exists, a
        `Certificate` saying `id: number` where Zoraxy answers a file name -- and each sat
        in the module a reader opens first.
        """
        ghost = self.panel.Source(rel="__ghost.ts", raw="", code="")
        ghost.types["Certificate"] = self.panel.TsType(name="Certificate")
        self.index.sources.append(ghost)
        self.addCleanup(self.index.sources.remove, ghost)
        self.assertEqual(self.gate.shadowed(self.index), [("__ghost.ts", "Certificate")])


class TheGateComparesEnoughToBeWorthFailingABuild(_ReadCase):
    """A gate that quietly compares nothing is as green as one that works."""

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.stats, cls.conflicts, cls.narrowed, cls.unexplained, cls.used = cls.gate.run(cls.index)

    def test_the_panel_agrees_with_itself_about_every_answer_it_reads(self) -> None:
        self.assertEqual(self.conflicts, [])
        self.assertEqual(self.gate.main([]), 0)

    def test_every_pair_it_will_not_compare_says_why_in_one_place(self) -> None:
        self.assertEqual(self.unexplained, [])
        for name in sorted(self.used):
            with self.subTest(group=name):
                self.assertGreater(len(self.gate.REASONS[name].strip()), 80)

    def test_no_reason_outlives_the_pair_it_was_written_for(self) -> None:
        self.assertEqual(sorted(set(self.gate.REASONS) - self.used), [])

    def test_most_of_what_the_panel_reads_is_actually_compared(self) -> None:
        self.assertGreaterEqual(self.stats["groups"], 25)
        self.assertGreaterEqual(self.stats["compared"], 200)
        self.assertLessEqual(self.stats["skipped"], self.stats["compared"] // 20)


class NothingElseEverChecksAGetAnswer(unittest.TestCase):
    """The premise, pinned: if either half of it stopped being true, delete this gate.

    A declared `T` on a GET is checked by nothing at either end. FastAPI validates a
    `response_model=` and serialises through it; not one route in `app/` sets one, so every
    GET answer is a bare dict or list assembled in Python and sent as-is. On the other end,
    `api.get<T>` casts through `unknown` and hands the caller the axios body untouched, so
    `T` is not checked at build time either -- `tsc` believes it by construction.

    That is the whole reason two declarations of one answer can disagree for a year without
    anything going red. If a `response_model=` ever appears, or the client starts validating,
    the disagreement becomes a real error somewhere and this gate is the wrong place to
    catch it.
    """

    def test_no_get_route_declares_a_response_model(self) -> None:
        for path in sorted((REPO_ROOT / "app").rglob("*.py")):
            with self.subTest(module=path.relative_to(REPO_ROOT).as_posix()):
                self.assertNotIn("response_model", path.read_text(encoding="utf-8"))

    def test_the_client_casts_rather_than_checks(self) -> None:
        client = (REPO_ROOT / "frontend" / "src" / "api" / "client.ts").read_text(
            encoding="utf-8"
        )
        self.assertIn("_axios.get<T>(url, config) as unknown as Promise<T>", client)
