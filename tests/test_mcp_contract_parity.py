"""A tool that declares less than its route enforces, and the two ways that ends badly.

An MCP tool publishes its parameters from its own signature. Nothing derives them from the
Pydantic model the route validates against -- a normal install serves no OpenAPI document
to read (`DEBUG` is false, so `openapi_url` is None) -- so the two drift silently, and a
comparison of methods and paths, which is all `scripts/check_api_mcp_parity.py` used to do,
sees nothing. Measured across the 20 tool calls that post a body to a modelled route: 26
divergences over 12 tools before this change, 0 outside three commented exemptions after.

Two of them could not be fixed by tightening a signature alone, and those are what this
file pins.

`apply_template` declared `target_port` and `domain` optional, then filled the holes with
`or 80` and `or ""`. Only one half of that was noisy. An empty domain is refused by
`ServiceIn` ("Invalid domain: a domain is required"), so the caller got a 422 and knew. A
port is different: `ServiceIn(..., target_port=80)` is valid, because 80 is a port. Every
call that named no port, against a template that sets none, created a service pointing at
port 80 and returned 200 -- and 80 is plausible enough that a reader of the service list
would not think twice. The tool now refuses, having sent nothing. A tool that invents a
value is worse than one that refuses: the refusal is read by whoever made the call, the
invention is read by whoever has to explain the outage.

`bulk_service_action` declared `action: str` where the route accepts exactly three words
and answers 400 for everything else. The cost was a round trip and an error message that
did not list the alternatives; with `Literal` the three words are in the schema, and the
wrong word never leaves the process. `delete` is in that list, so the round trip being
saved is one that deletes services.

`TheGateReadsWhatPydanticEnforces` is the ground truth underneath the checker itself. The
gate is pure AST: it cannot import `app` -- the security workflow's audit job installs
`pip-audit` and nothing else before running it -- so it reads requiredness, enums and
bounds out of annotations and validator bodies. This asserts that what it reads is what the
real models do when handed a value, which is the only thing that makes its verdict worth a
build failure.
"""

import ast
import importlib.util
import inspect
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastmcp.exceptions import ValidationError as _FastMCPRefusal
from fastmcp.tools import Tool
from pydantic import ValidationError

# What a tool raises when its own schema turns an argument away, before the body runs.
#
# `vauxtra_mcp/requirements.txt` asks for `fastmcp>=2.0` and pins nothing, and the versions
# that satisfy it disagree about this: 3.2.4 lets pydantic's own `ValidationError` out of
# `Tool.run()`, while 4.0.3 -- what CI resolves today -- wraps it in its own class. Naming
# only the wrapper made these two tests pass or fail on which version the resolver picked,
# over a refusal that works in both. The guarantee under test is that the call is refused
# and nothing is sent, not which library gets to name the refusal.
#
# The neighbouring assertions in this file say `ValueError`, which keeps working either way
# because pydantic's error is one. They are the reason only these two depended on which
# version was installed.
SchemaRefusal = (_FastMCPRefusal, ValidationError)

from app.api.providers import ProviderIn
from app.api.services import ServiceIn
from app.api.tags import TagIn
from app.api.templates import TemplateIn
from vauxtra_mcp import client as mcp_client
from vauxtra_mcp.tools.admin import create_tag as bridge_create_tag
from vauxtra_mcp.tools.providers import create_provider as bridge_create_provider
from vauxtra_mcp.tools.services import bulk_service_action as bridge_bulk_service_action
from vauxtra_mcp.tools.services import create_service as bridge_create_service
from vauxtra_mcp.tools.services import update_service as bridge_update_service
from vauxtra_mcp.tools.templates import apply_template as bridge_apply_template

REPO_ROOT = Path(__file__).resolve().parent.parent

# Two throwaway routes the witnesses below are measured against. A parameter and a response
# key, each written the way the real ones are, so a change that stops the collectors seeing
# them shows up here rather than as a gate that quietly reports nothing.
ROUTE_WITH_WITHDRAW = '''@router.delete("/api/things/{tid}")
def drop_thing(tid: int, withdraw: bool = False):
    return {}
'''
ROUTE_WITH_ERRORS = '''@router.post("/api/things")
def make_thing():
    return {"ok": True, "errors": []}
'''

# The same answer, wrapped the way a route says 207 rather than 200. `POST /api/services`
# is written exactly like this, and the wrapper alone kept it out of the pass below.
# A tool whose docstring names nothing it receives, for the witnesses below.
TOOL_SILENT_ABOUT_ERRORS = '''@mcp.tool()
def make_thing():
    """Make a thing."""
    return client.post("/things")
'''

ROUTE_WITH_WRAPPED_ERRORS = '''@router.post("/api/things")
def make_thing():
    return JSONResponse({"id": 1, "errors": errors}, status_code=201 if not errors else 207)
'''

# What `GET /api/templates/{id}/apply` answers, minus the two fields under test. A template
# is allowed to carry neither a domain nor a port; that is what lets one template serve
# several domains, and it is why the bridge cannot simply declare both required.
TEMPLATE_DEFAULTS = {
    "forward_scheme": "https",
    "websocket": False,
    "expose_mode": "proxy_dns",
    "proxy_provider_id": 2,
    "dns_provider_id": 3,
    "tunnel_provider_id": None,
    "public_target_mode": "manual",
    "dns_ip": "203.0.113.9",
    "tag_ids": [4],
    "icon_url": "",
    "_template_id": 7,
    "_template_name": "Standard HTTPS app",
}


# `expose_mode="tunnel"` is valid only alongside a tunnel provider. The rule lives in a
# `model_validator`, across two fields, and no per-parameter schema can carry it: the tool
# declares both fields correctly and the route still refuses the pair. It is pinned in
# `ARuleAcrossTwoFieldsStaysTheRoutesToSay` below rather than papered over here.
NEEDED_WITH = {"tunnel": {"tunnel_provider_id": 5}}


def _load_parity_gate():
    """`scripts/` is not a package, so the gate is loaded by path."""
    path = REPO_ROOT / "scripts" / "check_api_mcp_parity.py"
    spec = importlib.util.spec_from_file_location("check_api_mcp_parity", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakeResponse:
    """Stands in for the API answer, so nothing here opens a socket."""

    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def json(self) -> dict:
        return self._payload


class _BridgeCase(unittest.IsolatedAsyncioTestCase):
    """Runs a tool the way the server does, and records every request it makes."""

    # What every GET in this file answers with. Named for the first tool that needed one;
    # `update_service` reads a service back through the same seam.
    template_defaults: dict = TEMPLATE_DEFAULTS

    def setUp(self) -> None:
        self.sent: list[dict] = []

        def _get(path, **_kwargs):
            self.sent.append({"method": "GET", "path": path, "json": None})
            return _FakeResponse(dict(self.template_defaults))

        def _post(path, json=None, **_kwargs):
            self.sent.append({"method": "POST", "path": path, "json": json})
            return _FakeResponse({"id": 11, **(json or {})})

        def _put(path, json=None, **_kwargs):
            self.sent.append({"method": "PUT", "path": path, "json": json})
            return _FakeResponse({"id": 11, **(json or {})})

        self._patchers = [
            patch.object(mcp_client, "get", _get),
            patch.object(mcp_client, "post", _post),
            patch.object(mcp_client, "put", _put),
            patch.object(mcp_client, "check", lambda response: response),
        ]
        for p in self._patchers:
            p.start()

    def tearDown(self) -> None:
        for p in reversed(self._patchers):
            p.stop()

    @property
    def posted(self) -> list[dict]:
        return [call["json"] for call in self.sent if call["method"] == "POST"]

    @property
    def put_bodies(self) -> list[dict]:
        return [call["json"] for call in self.sent if call["method"] == "PUT"]


class ATemplateWithNoPortNoLongerInventsOne(_BridgeCase):
    """`apply_template` against a template that sets neither a port nor a domain."""

    def setUp(self) -> None:
        super().setUp()
        self.tool = Tool.from_function(bridge_apply_template)

    async def test_a_missing_port_refuses_instead_of_creating_a_service_on_80(self):
        with self.assertRaises(ValueError) as caught:
            await self.tool.run(
                {"template_id": 7, "subdomain": "vault", "target_ip": "10.0.0.9",
                 "domain": "example.com"}
            )
        self.assertIn("target_port", str(caught.exception))
        self.assertEqual(self.posted, [], "the bridge created a service on a port nobody chose")

    async def test_a_missing_domain_refuses_instead_of_sending_an_empty_one(self):
        with self.assertRaises(ValueError) as caught:
            await self.tool.run(
                {"template_id": 7, "subdomain": "vault", "target_ip": "10.0.0.9",
                 "target_port": 8200}
            )
        self.assertIn("domain", str(caught.exception))
        self.assertEqual(self.posted, [])

    async def test_the_refusal_names_the_template_and_both_missing_fields(self):
        with self.assertRaises(ValueError) as caught:
            await self.tool.run({"template_id": 7, "subdomain": "vault", "target_ip": "10.0.0.9"})
        message = str(caught.exception)
        for expected in ("Standard HTTPS app", "target_port", "domain"):
            self.assertIn(expected, message)

    async def test_the_arguments_are_enough_on_their_own(self):
        """Positive witness: the refusal is about having no value, not about the template."""
        await self.tool.run(
            {"template_id": 7, "subdomain": "vault", "target_ip": "10.0.0.9",
             "target_port": 8200, "domain": "example.com"}
        )
        self.assertEqual(len(self.posted), 1)
        self.assertEqual(self.posted[0]["target_port"], 8200)
        self.assertEqual(self.posted[0]["domain"], "example.com")
        self.assertEqual(self.posted[0]["forward_scheme"], "https", "template defaults lost")

    async def test_a_port_outside_the_range_never_leaves_the_bridge(self):
        """Refused by the schema, before the body runs: no template is even fetched."""
        with self.assertRaises(SchemaRefusal) as caught:
            await self.tool.run(
                {"template_id": 7, "subdomain": "vault", "target_ip": "10.0.0.9",
                 "target_port": 70000, "domain": "example.com"}
            )
        self.assertIn("65535", str(caught.exception))
        self.assertEqual(self.sent, [])


class ATemplateThatCarriesBothIsStillUsable(_BridgeCase):
    """The same tool against a template that does set a port and a domain."""

    template_defaults = {**TEMPLATE_DEFAULTS, "target_port": 443, "domain": "example.com"}

    def setUp(self) -> None:
        super().setUp()
        self.tool = Tool.from_function(bridge_apply_template)

    async def test_the_template_supplies_what_the_caller_omits(self):
        await self.tool.run({"template_id": 7, "subdomain": "vault", "target_ip": "10.0.0.9"})
        self.assertEqual(len(self.posted), 1)
        self.assertEqual(self.posted[0]["target_port"], 443)
        self.assertEqual(self.posted[0]["domain"], "example.com")

    async def test_an_argument_still_wins_over_the_template(self):
        await self.tool.run(
            {"template_id": 7, "subdomain": "vault", "target_ip": "10.0.0.9",
             "target_port": 8200, "domain": "other.example"}
        )
        self.assertEqual(self.posted[0]["target_port"], 8200)
        self.assertEqual(self.posted[0]["domain"], "other.example")

    async def test_what_is_posted_is_a_body_the_route_model_accepts(self):
        """The payload is only as good as `ServiceIn` says it is, so ask `ServiceIn`."""
        await self.tool.run({"template_id": 7, "subdomain": "vault", "target_ip": "10.0.0.9"})
        service = ServiceIn(**self.posted[0])
        self.assertEqual(service.target_port, 443)
        self.assertEqual(service.domain, "example.com")


class BulkActionsAreThreeWords(_BridgeCase):
    """`POST /api/services/bulk` accepts enable, disable and delete, and answers 400 else."""

    def setUp(self) -> None:
        super().setUp()
        self.tool = Tool.from_function(bridge_bulk_service_action)

    async def test_an_action_outside_the_enum_never_reaches_the_api(self):
        with self.assertRaises(SchemaRefusal) as caught:
            await self.tool.run({"service_ids": [1, 2], "action": "destroy"})
        self.assertIn("destroy", str(caught.exception))
        self.assertEqual(self.posted, [], "a refused action was sent anyway")

    async def test_the_schema_names_the_three_words_a_caller_may_use(self):
        action = self.tool.parameters["properties"]["action"]
        self.assertEqual(sorted(action["enum"]), ["delete", "disable", "enable"])

    async def test_each_of_the_three_is_passed_through_untouched(self):
        """Positive witness: the `Literal` refuses the fourth word and nothing else."""
        for action in ("enable", "disable", "delete"):
            await self.tool.run({"service_ids": [1, 2], "action": action})
        self.assertEqual([body["action"] for body in self.posted], ["enable", "disable", "delete"])
        self.assertEqual([body["ids"] for body in self.posted], [[1, 2]] * 3)


class SignaturesCarryWhatTheRouteEnforces(unittest.TestCase):
    """The published schemas, read the way a client reads them before calling."""

    def test_create_service_publishes_the_three_value_sets_and_the_port_range(self):
        properties = Tool.from_function(bridge_create_service).parameters["properties"]
        self.assertEqual(properties["forward_scheme"]["enum"], ["http", "https"])
        self.assertEqual(properties["expose_mode"]["enum"], ["proxy_dns", "tunnel"])
        self.assertEqual(properties["public_target_mode"]["enum"], ["auto", "manual"])
        # 0 is a service published in DNS alone; the route refuses it with a proxy or a
        # tunnel, a rule across two fields that no schema can carry (see below).
        self.assertEqual(properties["target_port"]["minimum"], 0)
        self.assertEqual(properties["target_port"]["maximum"], 65535)

    def test_create_tag_publishes_the_palette_the_api_silently_substitutes(self):
        properties = Tool.from_function(bridge_create_tag).parameters["properties"]
        self.assertEqual(len(properties["color"]["enum"]), 14)
        self.assertIn("secondary", properties["color"]["enum"])

    def test_create_provider_publishes_the_two_types_its_prose_had_forgotten(self):
        properties = Tool.from_function(bridge_create_provider).parameters["properties"]
        self.assertEqual(len(properties["type"]["enum"]), 10)
        for forgotten in ("powerdns", "desec"):
            self.assertIn(forgotten, properties["type"]["enum"])


class ARuleAcrossTwoFieldsStaysTheRoutesToSay(unittest.TestCase):
    """The limit of any signature-level contract, measured rather than assumed.

    `ServiceIn.validate_mode_dependencies` refuses `expose_mode="tunnel"` without a
    `tunnel_provider_id`. Both fields are declared exactly as the model declares them, and
    the call is still refused -- a schema describes parameters one at a time, and this rule
    is about a pair. The parity checker compares what a schema can express; this is what it
    cannot, and the reason the round trip is still the thing that decides.
    """

    BASE = {"subdomain": "a", "domain": "example.com", "target_ip": "10.0.0.9",
            "target_port": 8200}

    def test_tunnel_mode_without_a_tunnel_provider_is_refused_by_the_route(self):
        with self.assertRaises(ValidationError) as caught:
            ServiceIn(**self.BASE, expose_mode="tunnel")
        self.assertIn("Tunnel provider is required", str(caught.exception))

    def test_the_same_call_with_the_provider_is_accepted(self):
        service = ServiceIn(**self.BASE, expose_mode="tunnel", tunnel_provider_id=5)
        self.assertEqual(service.expose_mode, "tunnel")

    def test_the_bridge_declares_both_fields_so_the_pair_is_at_least_sendable(self):
        properties = Tool.from_function(bridge_create_service).parameters["properties"]
        self.assertIn("tunnel_provider_id", properties)
        self.assertIn("tunnel", properties["expose_mode"]["enum"])


class TheGateReadsWhatPydanticEnforces(unittest.TestCase):
    """The checker reads models with `ast`; these are the models answering for themselves.

    Every value set below is the one the gate extracted, not one written here: the test
    fails if the extraction changes, and it fails if a model stops enforcing what was
    extracted. Neither half is worth much alone.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.gate = _load_parity_gate()
        cls.models, cls.routes = cls.gate.collect_api_contracts(REPO_ROOT)

    def _enum(self, model_name: str, field: str) -> tuple[str, ...]:
        values = self.models[model_name].fields[field].enum
        self.assertTrue(values, f"the gate extracted no value set for {model_name}.{field}")
        return values

    def test_the_gate_agrees_with_pydantic_about_what_is_required(self):
        for name, model in (
            ("ServiceIn", ServiceIn), ("TemplateIn", TemplateIn),
            ("TagIn", TagIn), ("ProviderIn", ProviderIn),
        ):
            read = {f.name for f in self.models[name].fields.values() if f.required}
            enforced = {n for n, f in model.model_fields.items() if f.is_required()}
            self.assertEqual(read, enforced, f"{name}: the gate reads the wrong required set")

    def test_every_value_the_gate_calls_valid_is_accepted_by_the_model(self):
        service = {"subdomain": "a", "domain": "example.com", "target_ip": "10.0.0.9",
                   "target_port": 8200}
        for field in ("forward_scheme", "expose_mode", "public_target_mode"):
            for value in self._enum("ServiceIn", field):
                built = ServiceIn(**{**service, **NEEDED_WITH.get(value, {}), field: value})
                self.assertEqual(getattr(built, field), value)
        for value in self._enum("ProviderIn", "type"):
            self.assertEqual(ProviderIn(name="p", type=value, url="http://h").type, value)
        for value in self._enum("TagIn", "color"):
            self.assertEqual(TagIn(name="t", color=value).color, value)

    def test_a_value_outside_the_extracted_set_is_refused_or_replaced(self):
        service = {"subdomain": "a", "domain": "example.com", "target_ip": "10.0.0.9",
                   "target_port": 8200}
        for field in ("forward_scheme", "expose_mode", "public_target_mode"):
            with self.assertRaises(ValidationError, msg=f"ServiceIn accepted a bad {field}"):
                ServiceIn(**{**service, field: "nonsense"})
        with self.assertRaises(ValidationError):
            ProviderIn(name="p", type="nonsense", url="http://h")
        # Not a refusal: the route answers 200 and stores blue, which is why the bridge has
        # to carry the palette itself -- the caller is never told the colour was changed.
        self.assertEqual(TagIn(name="t", color="chartreuse").color, "blue")

    def test_the_port_bounds_the_gate_read_are_the_bounds_the_models_enforce(self):
        # A service may have no port (0): it is then a name in DNS and nothing forwards to
        # it. A template describes something a proxy forwards to, so it always has one.
        for name, model, build, lowest in (
            ("ServiceIn", ServiceIn,
             lambda port: {"subdomain": "a", "domain": "example.com", "target_ip": "10.0.0.9",
                           "target_port": port}, 0),
            ("TemplateIn", TemplateIn, lambda port: {"name": "t", "target_port": port}, 1),
        ):
            facts = self.models[name].fields["target_port"].facts
            self.assertEqual((facts.minimum, facts.maximum), (lowest, 65535), name)
            model(**build(lowest))
            model(**build(65535))
            for refused in (lowest - 1, 65536):
                with self.assertRaises(ValidationError, msg=f"{name} accepted port {refused}"):
                    model(**build(refused))

    def test_no_tool_diverges_from_its_route_outside_the_commented_exemptions(self):
        findings, _stats = self.gate.contract_findings(REPO_ROOT)
        divergent = [
            f.message for f in findings if f.key not in self.gate.ALLOWED_CONTRACT_DIVERGENCES
        ]
        self.assertEqual(divergent, [])

    def test_no_exemption_outlives_the_shape_it_was_written_for(self):
        findings, _stats = self.gate.contract_findings(REPO_ROOT)
        observed = {f.key for f in findings}
        stale = sorted(set(self.gate.ALLOWED_CONTRACT_DIVERGENCES) - observed)
        self.assertEqual(stale, [], "exemptions that no longer match anything")

    def test_the_comparison_covers_every_body_a_tool_sends_to_a_modelled_route(self):
        """A gate that quietly compares nothing passes just as green as one that works."""
        _findings, stats = self.gate.contract_findings(REPO_ROOT)
        self.assertGreaterEqual(stats["compared"], 20)
        self.assertEqual(stats["dict_body"], len(stats["unmodelled_routes"]))


# What `GET /api/services/{id}` answers with. `_service_to_payload` reads the relations out
# of `tags`/`environments` -- objects, not ids -- which is the shape that used to erase every
# label when a GET body was fed straight back to the PUT.
SERVICE_ROW = {
    "id": 11,
    "subdomain": "vault",
    "domain": "example.com",
    "target_ip": "10.0.0.9",
    "target_port": 8200,
    "forward_scheme": "https",
    "websocket": False,
    "enabled": True,
    "dns_provider_id": 3,
    "proxy_provider_id": 2,
    "tunnel_provider_id": None,
    "expose_mode": "proxy_dns",
    "public_target_mode": "manual",
    "auto_update_dns": False,
    "tunnel_hostname": "",
    "dns_ip": "203.0.113.9",
    "icon_url": "",
    "extra_proxy_provider_ids": [],
    "extra_dns_provider_ids": [],
    "tags": [{"id": 1, "name": "prod"}, {"id": 2, "name": "web"}],
    "environments": [{"id": 5, "name": "home"}],
}


class LabelsReachTheServiceOnCreate(_BridgeCase):
    """`create_service` sent `tag_ids: []` and declared no parameter to fill it.

    The bridge publishes eight tools for building tags and environments -- create, update,
    delete and list, twice over -- and, until these two parameters existed, nowhere to put
    one. A call naming a tag was refused by the schema before the body ran, which is how
    this was found: not by reading the signature, but by an agent trying to use it.

    `update_service` declared neither either, so the service could not be labelled
    afterwards. The only labelled service the bridge could produce came from
    `apply_template`, wearing whatever the template carried when it was applied.
    """

    def setUp(self) -> None:
        super().setUp()
        self.tool = Tool.from_function(bridge_create_service)

    async def test_the_ids_asked_for_are_the_ids_sent(self):
        await self.tool.run(
            {"subdomain": "vault", "domain": "example.com", "target_ip": "10.0.0.9",
             "target_port": 8200, "tag_ids": [4, 7], "environment_ids": [2]}
        )
        self.assertEqual(len(self.posted), 1)
        self.assertEqual(self.posted[0]["tag_ids"], [4, 7])
        self.assertEqual(self.posted[0]["environment_ids"], [2])

    async def test_a_service_created_without_them_still_carries_no_label(self):
        """Positive witness: the default is unchanged, so nothing gains a label by accident."""
        await self.tool.run(
            {"subdomain": "vault", "domain": "example.com", "target_ip": "10.0.0.9",
             "target_port": 8200}
        )
        self.assertEqual(self.posted[0]["tag_ids"], [])
        self.assertEqual(self.posted[0]["environment_ids"], [])


class LabelsAreReplacedNotMergedOnUpdate(_BridgeCase):
    """`PUT /api/services` replaces both lists; `set_tags` opens with a DELETE.

    So the overlay rule `update_service` states -- omitted fields keep their current value --
    has an edge worth pinning. Omitted, the labels survive because `_service_to_payload`
    reads them back off the GET. Named, they are the whole new set, and an empty list is a
    valid answer meaning none.
    """

    template_defaults = SERVICE_ROW

    def setUp(self) -> None:
        super().setUp()
        self.tool = Tool.from_function(bridge_update_service)

    async def test_omitting_them_keeps_the_labels_the_service_has(self):
        """The guarantee `_service_to_payload` already carried, now with a way past it."""
        await self.tool.run({"service_id": 11, "target_port": 9000})
        self.assertEqual(len(self.put_bodies), 1)
        self.assertEqual(self.put_bodies[0]["tag_ids"], [1, 2])
        self.assertEqual(self.put_bodies[0]["environment_ids"], [5])
        self.assertEqual(self.put_bodies[0]["target_port"], 9000, "the override was lost")

    async def test_a_list_replaces_every_label(self):
        await self.tool.run({"service_id": 11, "tag_ids": [3]})
        self.assertEqual(self.put_bodies[0]["tag_ids"], [3])
        self.assertEqual(self.put_bodies[0]["environment_ids"], [5], "environments moved too")

    async def test_an_empty_list_strips_them(self):
        await self.tool.run({"service_id": 11, "tag_ids": [], "environment_ids": []})
        self.assertEqual(self.put_bodies[0]["tag_ids"], [])
        self.assertEqual(self.put_bodies[0]["environment_ids"], [])

    async def test_environments_are_replaced_on_their_own(self):
        await self.tool.run({"service_id": 11, "environment_ids": [8, 9]})
        self.assertEqual(self.put_bodies[0]["environment_ids"], [8, 9])
        self.assertEqual(self.put_bodies[0]["tag_ids"], [1, 2], "tags moved with environments")


class EveryBodyFieldIsReachableOrExplained(unittest.TestCase):
    """The gate that would have caught this, and a witness that it can.

    The contract half next to it asks whether a tool declares what its route *requires*, and
    `ServiceIn` gives `tag_ids` and `environment_ids` a default. Nothing was required, so
    nothing was reported, for a field no caller could set. The pass below asks a different
    question: which keys does a write tool spell as a bare literal, where no argument can
    reach them.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.gate = _load_parity_gate()

    def _collect_from(self, source: str) -> list[tuple[str, str, str]]:
        """Run the collector over a throwaway tools module rather than the real bridge."""
        with tempfile.TemporaryDirectory() as tmp:
            tools = Path(tmp) / "vauxtra_mcp" / "tools"
            tools.mkdir(parents=True)
            (tools / "sample.py").write_text(source, encoding="utf-8")
            return self.gate.collect_unreachable_fields(Path(tmp))

    def test_no_body_field_is_unreachable_without_a_written_reason(self):
        unreachable = self.gate.collect_unreachable_fields(REPO_ROOT)
        unexplained = [
            f"{tool}.{name} is always {literal}"
            for tool, name, literal in unreachable
            if (tool, name) not in self.gate.ALLOWED_UNREACHABLE_FIELDS
        ]
        self.assertEqual(unexplained, [])

    def test_no_exemption_outlives_the_field_it_was_written_for(self):
        observed = {row[:2] for row in self.gate.collect_unreachable_fields(REPO_ROOT)}
        stale = sorted(set(self.gate.ALLOWED_UNREACHABLE_FIELDS) - observed)
        self.assertEqual(stale, [], "exemptions that no longer match anything")

    def test_the_pass_reads_something(self):
        """A collector that finds nothing passes as green as one that works."""
        self.assertGreaterEqual(len(self.gate.collect_unreachable_fields(REPO_ROOT)), 11)

    def test_the_two_fields_this_was_written_for_are_reachable_now(self):
        unreachable = {row[:2] for row in self.gate.collect_unreachable_fields(REPO_ROOT)}
        for tool in ("create_service", "update_service"):
            for name in ("tag_ids", "environment_ids"):
                self.assertNotIn((tool, name), unreachable)
                self.assertNotIn(
                    (tool, name),
                    self.gate.ALLOWED_UNREACHABLE_FIELDS,
                    "reachable fields do not need an exemption",
                )

    def test_a_hardcoded_key_with_no_parameter_is_reported(self):
        found = self._collect_from(
            "@mcp.tool()\n"
            "def make_thing(name: str):\n"
            '    payload = {"name": name, "tag_ids": []}\n'
            '    return client.post("/things", json=payload)\n'
        )
        self.assertEqual(found, [("make_thing", "tag_ids", "[]")])

    def test_a_key_a_parameter_feeds_is_not_reported(self):
        """Positive witness: the reporting is about reachability, not about the value."""
        found = self._collect_from(
            "@mcp.tool()\n"
            "def make_thing(name: str, tag_ids: list[int] | None = None):\n"
            '    payload = {"name": name, "tag_ids": tag_ids or []}\n'
            '    return client.post("/things", json=payload)\n'
        )
        self.assertEqual(found, [])

    def test_a_dict_that_is_not_a_body_is_not_reported(self):
        """A tool is free to build dicts for itself; only what it sends is a contract."""
        found = self._collect_from(
            "@mcp.tool()\n"
            "def make_thing(name: str):\n"
            '    labels = {"tag_ids": [], "environment_ids": []}\n'
            '    return client.post("/things", json={"name": name, "count": len(labels)})\n'
        )
        self.assertEqual(found, [])

    def test_a_tool_that_only_reads_is_not_reported(self):
        found = self._collect_from(
            "@mcp.tool()\n"
            "def list_things():\n"
            '    return client.get("/things", params={"tag_ids": []})\n'
        )
        self.assertEqual(found, [])


def _sample_repo(tmp: str, route_source: str = "", tool_source: str = "") -> Path:
    """A throwaway repo carrying the two halves the query and answer passes read."""
    root = Path(tmp)
    api = root / "app" / "api"
    tools = root / "vauxtra_mcp" / "tools"
    api.mkdir(parents=True)
    tools.mkdir(parents=True)
    (api / "sample.py").write_text(route_source, encoding="utf-8")
    (tools / "sample.py").write_text(tool_source, encoding="utf-8")
    return root


class EveryQueryParameterIsReachableOrExplained(unittest.TestCase):
    """The direction nothing was asking, and the parameter that hid in it.

    `compare_contract` reports a query key a tool *sends* that its route does not read. The
    reverse question -- a parameter the route reads that no tool can send -- was never put,
    and a parameter no tool sends is invisible to a check that only reads what tools send.
    `DELETE /api/providers/{pid}` takes `withdraw`, which decides whether the provider's
    proxy hosts and DNS records come down before it is forgotten or stay live on a provider
    Vauxtra no longer knows about. The panel offers it as a checkbox and the API tests cover
    both branches; the bridge could reach neither, so an agent's only possible provider
    deletion was the one that leaves published records behind.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.gate = _load_parity_gate()

    def _collect_from(self, route_source: str, tool_source: str) -> list:
        with tempfile.TemporaryDirectory() as tmp:
            return self.gate.collect_unreachable_query(
                _sample_repo(tmp, route_source, tool_source)
            )

    def test_no_query_parameter_is_unreachable_without_a_written_reason(self):
        unreachable = self.gate.collect_unreachable_query(REPO_ROOT)
        unexplained = [
            f"{method} {path} ?{name}"
            for method, path, name, _servers in unreachable
            if (method, path, name) not in self.gate.ALLOWED_UNREACHABLE_QUERY
        ]
        self.assertEqual(unexplained, [])

    def test_no_exemption_outlives_the_parameter_it_was_written_for(self):
        observed = {row[:3] for row in self.gate.collect_unreachable_query(REPO_ROOT)}
        stale = sorted(set(self.gate.ALLOWED_UNREACHABLE_QUERY) - observed)
        self.assertEqual(stale, [], "exemptions that no longer match anything")

    def test_the_parameter_this_was_written_for_is_reachable_now(self):
        """`withdraw` specifically, read off the tool rather than off the gate."""
        from vauxtra_mcp.tools.providers import delete_provider

        self.assertIn("withdraw", inspect.signature(delete_provider).parameters)
        self.assertIn('params["withdraw"] = "true"', inspect.getsource(delete_provider))

    def test_a_parameter_no_serving_tool_mentions_is_reported(self):
        found = self._collect_from(
            ROUTE_WITH_WITHDRAW,
            "@mcp.tool()\ndef drop_thing(thing_id: int):\n"
            '    return client.delete(f"/things/{thing_id}")\n',
        )
        self.assertEqual(
            [(m, p, n) for m, p, n, _ in found],
            [("DELETE", "/api/things/{}", "withdraw")],
        )

    def test_a_parameter_the_tool_can_send_is_not_reported(self):
        """The check is about reachability, not about how the tool spells the send.

        The shape below is the one the bridge actually uses, and it is not an inline literal
        dict: reading the call would miss it. That is why the collector looks for the name
        anywhere in the tool's source instead, which errs towards calling a parameter
        reachable rather than crying wolf over one that is.
        """
        found = self._collect_from(
            ROUTE_WITH_WITHDRAW,
            "@mcp.tool()\ndef drop_thing(thing_id: int, withdraw: bool = False):\n"
            "    params = {}\n"
            "    if withdraw:\n"
            '        params["withdraw"] = "true"\n'
            '    return client.delete(f"/things/{thing_id}", params=params or None)\n',
        )
        self.assertEqual(found, [])

    def test_a_route_no_tool_serves_is_left_to_the_api_only_table(self):
        """An unserved route is a coverage question, and it has its own table and reason."""
        found = self._collect_from(
            '@router.get("/api/things")\ndef list_things(q: str = ""):\n    return []\n',
            '@mcp.tool()\ndef unrelated():\n    return client.get("/other")\n',
        )
        self.assertEqual(found, [])


class EveryPartialFailureKeyIsNamedByItsTool(unittest.TestCase):
    """What the tool says about the half of the work that did not happen.

    FastMCP publishes the signature and the docstring, and nothing else: for an agent, the
    docstring is the whole description of the answer. Several routes here answer a partial
    success -- `{"ok": true, "errors": [...]}` from a service deletion whose provider
    records are still live, `not_applied` from a settings save the running scheduler never
    received -- and nine tools returned one of those keys without naming it, ten pairs of
    tool and key in all. Four such pairs, across three tools, already named theirs, which
    makes this drift from a house rule rather than a convention nobody had adopted.

    Two of the nine were found only after this pass learnt to read through a `JSONResponse`
    wrapper. `POST /api/services` answers `{"id", "fqdn", "errors"}` with a 207 when the row
    was stored but the proxy host, the DNS record or the tunnel route was not published, and
    `create_service` -- the most-used write tool in the bridge -- described that answer as
    "the created service record". An agent reading it reported a service created and
    reachable when nothing routed to its hostname.

    `not_applied` is the clearest of them. Every other consumer reads it: the route builds
    it, `tests/test_settings_applied.py` pins it, `frontend/src/types/api.ts` declares it,
    `GeneralTab.tsx` renders it and all eight locales translate the sentence. The bridge
    alone was silent, on the one surface with no human reading the screen.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.gate = _load_parity_gate()

    def _collect_from(self, route_source: str, tool_source: str) -> list:
        with tempfile.TemporaryDirectory() as tmp:
            return self.gate.collect_silent_partials(
                _sample_repo(tmp, route_source, tool_source)
            )

    def test_no_tool_stays_silent_about_one_without_a_written_reason(self):
        silent = self.gate.collect_silent_partials(REPO_ROOT)
        unexplained = [
            f"{tool} says nothing about `{key}` from {method} {path}"
            for tool, key, method, path in silent
            if (tool, key) not in self.gate.ALLOWED_SILENT_PARTIALS
        ]
        self.assertEqual(unexplained, [])

    def test_no_exemption_outlives_the_silence_it_was_written_for(self):
        observed = {row[:2] for row in self.gate.collect_silent_partials(REPO_ROOT)}
        stale = sorted(set(self.gate.ALLOWED_SILENT_PARTIALS) - observed)
        self.assertEqual(stale, [], "exemptions that no longer match anything")

    def test_the_pass_reads_something(self):
        """A collector that reads no route answers passes as green as one that works."""
        answers = self.gate.collect_route_answers(REPO_ROOT)
        self.assertIn("not_applied", answers[("POST", "/api/settings")])
        self.assertIn("errors", answers[("DELETE", "/api/services/{}")])
        self.assertIn("skipped", answers[("POST", "/api/docker/import")])
        # And the wrapped shape, which is the one this pass was blind to at first.
        self.assertIn("errors", answers[("POST", "/api/services")])

    def test_the_nine_this_was_written_for_name_their_keys_now(self):
        from vauxtra_mcp.tools.admin import save_settings
        from vauxtra_mcp.tools.providers import delete_provider
        from vauxtra_mcp.tools.services import (
            bulk_service_action,
            create_service,
            delete_service,
            import_docker_containers,
            toggle_service,
            update_service,
        )
        from vauxtra_mcp.tools.templates import apply_template

        expected = [
            (save_settings, ["not_applied"]),
            (delete_service, ["errors"]),
            (bulk_service_action, ["errors"]),
            (toggle_service, ["errors"]),
            (update_service, ["errors"]),
            (delete_provider, ["errors"]),
            (import_docker_containers, ["errors", "skipped"]),
            (create_service, ["errors"]),
            (apply_template, ["errors"]),
        ]
        for tool, keys in expected:
            doc = inspect.getdoc(tool) or ""
            for key in keys:
                self.assertIn(key, doc, f"{tool.__name__} says nothing about {key}")

    def test_a_key_the_docstring_never_names_is_reported(self):
        found = self._collect_from(
            ROUTE_WITH_ERRORS,
            '@mcp.tool()\ndef make_thing():\n    """Make a thing."""\n'
            '    return client.post("/things")\n',
        )
        self.assertEqual(found, [("make_thing", "errors", "POST", "/api/things")])

    def test_a_key_inside_a_jsonresponse_wrapper_is_reported(self):
        """The shape that hid two of the nine: the dict is an argument, not the return value.

        Reading only `return {...}` saw nothing here, and a pass that reads nothing is as
        green as one that works. This is the witness for that, so the wrapper cannot go
        unread again.
        """
        found = self._collect_from(ROUTE_WITH_WRAPPED_ERRORS, TOOL_SILENT_ABOUT_ERRORS)
        self.assertEqual(found, [("make_thing", "errors", "POST", "/api/things")])

    def test_a_key_the_docstring_names_is_not_reported(self):
        found = self._collect_from(
            ROUTE_WITH_ERRORS,
            "@mcp.tool()\ndef make_thing():\n"
            '    """Make a thing. `errors` holds what could not be published."""\n'
            '    return client.post("/things")\n',
        )
        self.assertEqual(found, [])

    def test_an_ordinary_key_is_not_a_partial_failure(self):
        """The vocabulary is the point: a tool need not recite every field it returns."""
        found = self._collect_from(
            '@router.post("/api/things")\ndef make_thing():\n'
            '    return {"id": 1, "name": "x"}\n',
            '@mcp.tool()\ndef make_thing():\n    """Make a thing."""\n'
            '    return client.post("/things")\n',
        )
        self.assertEqual(found, [])


def _half_succeeds_table(readme_text: str) -> set[tuple[str, str]]:
    """(tool, key) pairs out of the README's "When a call half-succeeds" table.

    Read line by line and scoped to that one section, so the tool names in "Available
    tools" are not swept in and the prose around it is free to say `errors` without
    counting as a row. Only the first two columns are read: the third says what a
    non-empty one means, and a tool named there is a sentence, not a claim.

    A heading that is renamed reads as an empty table rather than as an error, which is
    why the comparison below is made in both directions: an empty table disagrees with
    every pair the code answers, so the rename fails the build instead of passing it.
    """
    rows: set[tuple[str, str]] = set()
    inside = False
    for line in readme_text.splitlines():
        if line.startswith("## "):
            inside = line.startswith("## When a call half-succeeds")
            continue
        if not inside or not line.startswith("| `"):
            continue
        cells = line.split("|")
        keys = [part for i, part in enumerate(cells[1].split("`")) if i % 2]
        tools = [part for i, part in enumerate(cells[2].split("`")) if i % 2]
        for key in keys:
            for tool in tools:
                rows.add((tool, key))
    return rows


class TheHalfSucceedsTableNamesWhatTheCodeAnswers(unittest.TestCase):
    """The README's table of partial-failure keys, against the keys the routes answer with.

    `EveryPartialFailureKeyIsNamedByItsTool` pins the docstrings, which is what an agent
    reads at call time. The README is what a person reads before writing the integration,
    and it carries the same pairs written out by hand -- the kind of list that is
    correct on the day it is written and quietly wrong two routes later. So it is compared
    to the code rather than trusted, in both directions: a pair the README omits is a
    partial failure nobody was warned about, and a pair it invents sends a reader looking
    for a key that is not there.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.gate = _load_parity_gate()

    def _documented(self) -> set[tuple[str, str]]:
        readme = (REPO_ROOT / "vauxtra_mcp" / "README.md").read_text(encoding="utf-8")
        return _half_succeeds_table(readme)

    def _answered(self) -> set[tuple[str, str]]:
        answers = self.gate.collect_route_answers(REPO_ROOT)
        pairs: set[tuple[str, str]] = set()
        for name, tool in self.gate.collect_mcp_contracts(REPO_ROOT).items():
            for call in tool.calls:
                for key in answers.get((call.method, call.path), set()):
                    if key in self.gate.PARTIAL_FAILURE_KEYS:
                        pairs.add((name, key))
        return pairs

    def test_the_table_lists_every_pair_and_invents_none(self):
        documented, answered = self._documented(), self._answered()
        self.assertEqual(
            sorted(answered - documented), [], "answered by a route, missing from the README"
        )
        self.assertEqual(
            sorted(documented - answered), [], "in the README, answered by no route"
        )

    def test_the_parser_reads_something(self):
        """An equality of two empty sets is as green as an equality of the right ones."""
        self.assertIn(("create_service", "errors"), self._documented())

    def test_the_parser_reads_only_its_own_section(self):
        readme = """## Available tools

| Tool | What it does |
| --- | --- |
| `sync_services_from_providers` | reads the providers |

## When a call half-succeeds

| Key | Answered by | What a non-empty one means |
| --- | --- | --- |
| `errors` | `delete_service`, `delete_provider` | still live on their provider |

## Example prompts

Ask for `errors` and it will not become a row.
"""
        self.assertEqual(
            _half_succeeds_table(readme),
            {("delete_service", "errors"), ("delete_provider", "errors")},
        )

    def test_a_renamed_section_reads_as_empty_rather_than_as_agreement(self):
        readme = """## When a call goes half well

| Key | Answered by | What a non-empty one means |
| --- | --- | --- |
| `errors` | `delete_service` | still live on its provider |
"""
        self.assertEqual(_half_succeeds_table(readme), set())


#: Tools that reach an `admin` route through a client the join cannot see.
#:
#: `_routes_needing_admin` reads the app, and `collect_mcp_contracts` reads `client.get`,
#: `client.post` and their siblings. `stream_logs_snapshot` opens its own `httpx` client to
#: hold the SSE socket open -- the shared `client` has no streaming verb -- so the route it
#: calls is invisible to the join, and the README naming it would read as a name invented.
#: Teaching the collector a second call shape would put a streaming route into every other
#: pass that assumes a request and a response, which is a wide change for one tool.
#:
#: So the tool is named here with the route it reaches, and
#: `test_the_exemption_still_calls_the_route_it_names` fails if either half stops being
#: true: the tool no longer opening that path, or that path no longer asking for `admin`.
#: An exemption cannot outlive what it was written for, which is the rule the
#: `ALLOWED_*` sets in the gate itself are held to.
ADMIN_THROUGH_OWN_CLIENT = {"stream_logs_snapshot": ("GET", "/api/logs/stream")}

def _routes_needing_admin(repo_root: Path, normalize) -> set[tuple[str, str]]:
    """Every (verb, path) whose body asks `require_auth(request, scope="admin")`.

    Read off the AST rather than off the router. FastAPI knows the verb and the path; only
    the source knows what the body will demand of the caller, because the scope is an
    argument to a call inside it. A route may ask twice -- `save_settings` asks for `write`
    and then for `admin` when the body carries a setting that chooses a URL the server goes
    and fetches -- and that counts: a `write` key cannot make that call in full.
    """
    found: set[tuple[str, str]] = set()
    for path in sorted((repo_root / "app" / "api").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            routes = []
            for dec in node.decorator_list:
                if not isinstance(dec, ast.Call) or not isinstance(dec.func, ast.Attribute):
                    continue
                if getattr(dec.func.value, "id", "") != "router":
                    continue
                if dec.args and isinstance(dec.args[0], ast.Constant):
                    routes.append((dec.func.attr.upper(), normalize(dec.args[0].value)))
            if not routes:
                continue
            for inner in ast.walk(node):
                if not isinstance(inner, ast.Call):
                    continue
                if getattr(inner.func, "id", "") not in ("require_auth", "require_auth_or_setup"):
                    continue
                for kw in inner.keywords:
                    if (
                        kw.arg == "scope"
                        and isinstance(kw.value, ast.Constant)
                        and kw.value.value == "admin"
                    ):
                        found.update(routes)
    return found


def _admin_tool_list(readme_text: str, tool_names: set[str]) -> set[str]:
    """The tool names on the Prerequisites line that says which tools need `admin`."""
    for line in readme_text.splitlines():
        if not line.startswith("2. An API key"):
            continue
        backticked = [part for i, part in enumerate(line.split("`")) if i % 2]
        return {name for name in backticked if name in tool_names}
    return set()


class TheAdminToolListNamesEveryToolThatNeedsAdmin(unittest.TestCase):
    """The README's list of tools a `write` key cannot use, against the routes they call.

    This is the first thing a reader does before minting a key, and getting it wrong costs
    them in the direction that hurts: a tool the list forgets is one they find out about
    when a 403 lands in the middle of a restore, and a scope named too low is a key handed
    out with less reach than the work needs. It was written as prose -- "backup/restore,
    factory reset, API key management" -- which reads well and cannot be checked, and it had
    already drifted: `mark_setup_complete` asks for `admin` and no category named it, and
    `save_settings` asks for it on one body shape and none mentioned that either.

    So the list names every tool outright and is compared to the routes in both directions.
    A tool missing from it is a 403 nobody was warned about; a tool invented in it is a key
    minted stronger than the work needed.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.gate = _load_parity_gate()

    def _needing(self) -> set[str]:
        admin = _routes_needing_admin(REPO_ROOT, self.gate.normalize)
        return {
            name
            for name, tool in self.gate.collect_mcp_contracts(REPO_ROOT).items()
            if any((call.method, call.path) in admin for call in tool.calls)
        } | set(ADMIN_THROUGH_OWN_CLIENT)

    def test_the_exemption_still_calls_the_route_it_names(self):
        """Both halves of `ADMIN_THROUGH_OWN_CLIENT`, so it cannot outlive its reason.

        A name added here is a name the README must carry, and nothing else checks it. If
        the tool stops opening that path, or the path stops asking for `admin`, the entry
        would go on demanding a line in the README that would then be wrong.
        """
        admin = _routes_needing_admin(REPO_ROOT, self.gate.normalize)
        tools = self.gate.collect_mcp_contracts(REPO_ROOT)
        for name, (method, path) in ADMIN_THROUGH_OWN_CLIENT.items():
            with self.subTest(tool=name):
                self.assertIn(name, tools)
                source = (REPO_ROOT / "vauxtra_mcp" / "tools" / tools[name].module).read_text(
                    encoding="utf-8"
                )
                body = source.split("def " + name + "(", 1)[1].split("@mcp.tool()", 1)[0]
                self.assertIn(path, body)
                self.assertIn(method, body)
                self.assertIn((method, path), admin)

    def test_the_list_names_every_tool_and_invents_none(self):
        readme = (REPO_ROOT / "vauxtra_mcp" / "README.md").read_text(encoding="utf-8")
        named = _admin_tool_list(readme, self.gate.collect_mcp_tools(REPO_ROOT))
        self.assertEqual(named, self._needing())

    def test_both_halves_read_something(self):
        """A collector that finds nothing agrees with a list that names nothing."""
        admin = _routes_needing_admin(REPO_ROOT, self.gate.normalize)
        self.assertIn(("POST", "/api/logs/clear"), admin)
        self.assertIn(("POST", "/api/auth/change-password"), admin)
        self.assertIn(("POST", "/api/reset"), admin)
        self.assertGreater(len(self._needing()), 5)

    def test_a_tool_named_in_prose_rather_than_in_backticks_does_not_count(self):
        """The shape this replaced. "API key management" covers three tools and names none."""
        line = "2. An API key -- scopes are `read`, `write` and `admin`; the admin ones are "
        line += "`change_password`, backup/restore, factory reset, API key management."
        named = _admin_tool_list(line, self.gate.collect_mcp_tools(REPO_ROOT))
        self.assertEqual(named, {"change_password"})

    def test_a_renamed_item_reads_as_empty_rather_than_as_agreement(self):
        """Losing the line must fail the build, not pass it for want of anything to compare."""
        readme = (REPO_ROOT / "vauxtra_mcp" / "README.md").read_text(encoding="utf-8")
        moved = readme.replace("2. An API key", "2. An API token")
        self.assertEqual(_admin_tool_list(moved, self.gate.collect_mcp_tools(REPO_ROOT)), set())


if __name__ == "__main__":
    unittest.main(verbosity=2)
