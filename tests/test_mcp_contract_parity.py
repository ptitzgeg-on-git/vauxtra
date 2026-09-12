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

import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch

from fastmcp.exceptions import ValidationError as SchemaRefusal
from fastmcp.tools import Tool
from pydantic import ValidationError

from app.api.providers import ProviderIn
from app.api.services import ServiceIn
from app.api.tags import TagIn
from app.api.templates import TemplateIn
from vauxtra_mcp import client as mcp_client
from vauxtra_mcp.tools.admin import create_tag as bridge_create_tag
from vauxtra_mcp.tools.providers import create_provider as bridge_create_provider
from vauxtra_mcp.tools.services import bulk_service_action as bridge_bulk_service_action
from vauxtra_mcp.tools.services import create_service as bridge_create_service
from vauxtra_mcp.tools.templates import apply_template as bridge_apply_template

REPO_ROOT = Path(__file__).resolve().parent.parent

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

    template_defaults: dict = TEMPLATE_DEFAULTS

    def setUp(self) -> None:
        self.sent: list[dict] = []

        def _get(path, **_kwargs):
            self.sent.append({"method": "GET", "path": path, "json": None})
            return _FakeResponse(dict(self.template_defaults))

        def _post(path, json=None, **_kwargs):
            self.sent.append({"method": "POST", "path": path, "json": json})
            return _FakeResponse({"id": 11, **(json or {})})

        self._patchers = [
            patch.object(mcp_client, "get", _get),
            patch.object(mcp_client, "post", _post),
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
        self.assertEqual(properties["target_port"]["minimum"], 1)
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
        for name, model, build in (
            ("ServiceIn", ServiceIn,
             lambda port: {"subdomain": "a", "domain": "example.com", "target_ip": "10.0.0.9",
                           "target_port": port}),
            ("TemplateIn", TemplateIn, lambda port: {"name": "t", "target_port": port}),
        ):
            facts = self.models[name].fields["target_port"].facts
            self.assertEqual((facts.minimum, facts.maximum), (1, 65535), name)
            model(**build(1))
            model(**build(65535))
            for refused in (0, 65536):
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
