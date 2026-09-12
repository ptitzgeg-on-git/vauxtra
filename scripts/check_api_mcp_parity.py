"""Every API route must be reachable through the MCP bridge, documented, and declared with
the contract the route enforces.

The gate used to compare paths alone, and a path is not a route. `PUT /api/templates/{tid}`
counted as covered because `get_template` and `delete_template` mention the same path: the
bridge could create a template and delete one, but not edit one, and this script reported
full parity while that was true. It now compares (method, path), which is what a route is.

It also checks the bridge's README against the tools that exist. That list had drifted to
31 of 84 tools and omitted two whole modules -- an agent reading it would conclude the
bridge could not manage templates, settings, webhooks or API keys at all. A tool nobody
knows about is as unreachable as one that was never written, so the drift fails the build.

Reaching a route is not the same as calling it correctly, and that was the third gap. A
tool declares its parameters by hand -- FastMCP reads the signature, and a normal install
publishes no schema to derive them from (`DEBUG` is false, so `openapi_url` is None) -- so
nothing but this script compares a signature with the model the route validates against.
`apply_template` declared `target_port` optional and filled the hole with `or 80`: every
call that named no port created a service pointing at port 80, which the API accepts
because 80 is a port. `bulk_service_action` declared `action: str` where the route allows
three words. Neither was visible to a check that compares methods and paths.

So the contract half below reads both sides and compares them field by field:

  * a field the model requires must be carried by a parameter the tool requires -- not by
    an optional one, and not by a fallback that invents a value;
  * a field the route restricts to a set of values must be a `Literal` of exactly that set
    on the tool, whether the route refuses the others or quietly replaces them;
  * a payload key the model does not declare is an error, silent or 422;
  * types, numeric bounds and list minimums must agree.

Two shapes are recognized as bridge conveniences rather than divergences, because the
comparison would otherwise be meaningless rather than merely noisy:

  * a *derived* payload -- one built from a helper or a `**` merge, as `update_service` and
    `toggle_service` build theirs by reading the service back first. What the tool declares
    is an overlay on a full body that came from the API, so requiredness is answered by the
    GET, not by the signature. Value constraints are still compared for every parameter the
    overlay does name.
  * a route whose body is a plain `dict` (five of them: settings, domains, environments,
    webhooks, alerts). There is no model, so there is no contract to compare. They are
    counted and printed rather than passed over in silence.

Anything else that is deliberate needs an entry in ALLOWED_CONTRACT_DIVERGENCES, with the
reason written next to it, and an entry that stops matching fails the build like a stale
route exemption does.
"""
import argparse
import ast
import re
import sys
from dataclasses import dataclass, field, replace
from pathlib import Path

API_PATTERN = re.compile(r'@router\.(get|post|put|delete|patch)\("(/api[^"\\)]*)"')
MCP_PATTERN = re.compile(r'client\.(get|post|put|delete|patch)\(\s*f?"(/[^"\\)]*)"')

# Routes the bridge deliberately does not expose, each with the reason it is not a gap.
ALLOWED_API_ONLY = {
    # A continuous SSE stream has no place in a request/response tool; `stream_logs_snapshot`
    # reads a bounded slice of it with its own client.
    ("GET", "/api/logs/stream"),
    # The deprecated GET alias of `POST /api/services/{sid}/check`, kept one version for
    # existing scripts. `check_service_health` calls the POST; offering an agent the alias
    # would be handing it a route we are in the middle of removing.
    ("GET", "/api/services/{}/check"),
    # Raw per-provider record editing. A service is the bridge's unit of work: it pushes a
    # service and the provider rows follow, so reaching underneath is a way to create drift.
    ("GET", "/api/providers/{}/dns-records"),
    ("POST", "/api/providers/{}/dns-records"),
    ("DELETE", "/api/providers/{}/dns-records/{}"),
    ("GET", "/api/providers/{}/proxy-hosts"),
    ("POST", "/api/providers/{}/proxy-hosts"),
    ("DELETE", "/api/providers/{}/proxy-hosts/{}"),
}

# (tool, field or parameter, finding kind) -> why the divergence is the right call.
#
# Every entry is checked against what this run observed: one that no longer matches fails
# the build, so an exemption cannot outlive the shape it was written for.
ALLOWED_CONTRACT_DIVERGENCES = {
    # `apply_template` merges three sources for these two fields: the argument, then the
    # template's own default, then nothing. `POST /api/services` requires both, and the
    # template legitimately carries neither -- an empty `domain` and a null `target_port`
    # are what makes a template a template (`app/api/templates.py`). Demanding them on the
    # tool would make every template's domain and port dead weight. What is not allowed is
    # the third case: when neither source has a value the tool raises and posts nothing,
    # instead of inventing 80 and "" the way it used to.
    ("apply_template", "target_port", "required-from-expression"):
        "merged from the template, and refused outright when neither side carries one",
    ("apply_template", "domain", "required-from-expression"):
        "merged from the template, and refused outright when neither side carries one",
    # `ProviderUpdate.enabled` is `int | None` because the column is an INTEGER, and pydantic
    # turns the tool's `True` into the 1 the column wants. The tool keeps `bool`: the column
    # holds a flag, not a number, and an `int` in the schema would invite an agent to send 2
    # -- which the route would store, and nothing would ever read as anything but truthy.
    ("update_provider", "enabled", "type-mismatch"):
        "the column is a 0/1 flag; bool is the honest type and pydantic coerces it to int",
}

_CONTAINER_TYPES = {"list", "dict", "set", "tuple", "frozenset"}

# Filled by `collect_api_contracts`: helper name -> the bounds it enforces. A validator
# that hands the value to `is_valid_port` says nothing about ports on its own face.
_RANGE_PREDICATES: dict[str, tuple[int, int]] = {}


def normalize(path: str) -> str:
    p = path.strip()
    if not p.startswith("/api"):
        p = "/api" + p
    return re.sub(r"\{[^}]+\}", "{}", p)


def _collect(files, pattern) -> set[tuple[str, str]]:
    routes: set[tuple[str, str]] = set()
    for file_path in files:
        text = file_path.read_text(encoding="utf-8")
        for method, path in pattern.findall(text):
            routes.add((method.upper(), normalize(path)))
    return routes


def collect_api_routes(repo_root: Path) -> set[tuple[str, str]]:
    return _collect((repo_root / "app" / "api").glob("*.py"), API_PATTERN)


def collect_mcp_routes(repo_root: Path) -> set[tuple[str, str]]:
    return _collect((repo_root / "vauxtra_mcp" / "tools").glob("*.py"), MCP_PATTERN)


def collect_mcp_tools(repo_root: Path) -> set[str]:
    """Every function decorated with `@mcp.tool()`, read from the AST rather than grepped."""
    tools: set[str] = set()
    for file_path in (repo_root / "vauxtra_mcp" / "tools").glob("*.py"):
        tree = ast.parse(file_path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            if _is_tool(node):
                tools.add(node.name)
    return tools


def collect_documented_tools(repo_root: Path) -> set[str]:
    """Tool names in the first column of the README's "Available tools" tables.

    Only that column, so the prose is free to mention `read`, `write` or `VAUXTRA_URL`
    without either passing for a tool or being reported as one that does not exist.
    """
    text = (repo_root / "vauxtra_mcp" / "README.md").read_text(encoding="utf-8")
    start = text.find("## Available tools")
    if start == -1:
        return set()
    end = text.find("\n## ", start + 1)
    section = text[start:end if end != -1 else len(text)]

    documented: set[str] = set()
    for line in section.splitlines():
        if not line.startswith("| `"):
            continue
        first_cell = line.split("|")[1]
        documented.update(re.findall(r"`([a-z_][a-z0-9_]*)`", first_cell))
    return documented


# ---------------------------------------------------------------------------------------
# Reading declarations out of the AST
# ---------------------------------------------------------------------------------------


@dataclass(frozen=True)
class TypeFacts:
    """What an annotation promises: base type, whether None is allowed, and its value set."""

    base: str = "any"
    nullable: bool = False
    enum: tuple[str, ...] = ()       # a scalar restricted to these values
    item_enum: tuple[str, ...] = ()  # a list whose items are restricted to these values
    min_items: int | None = None
    minimum: int | None = None
    maximum: int | None = None


def _is_tool(node: ast.FunctionDef) -> bool:
    return any(
        isinstance(d, ast.Call) and getattr(d.func, "attr", "") == "tool"
        for d in node.decorator_list
    )


def _call_name(node: ast.AST) -> str:
    if not isinstance(node, ast.Call):
        return ""
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return ""


def _string_members(node: ast.AST) -> tuple[str, ...] | None:
    """The string members of a literal collection, or None when it is not one.

    A dict answers with its keys: `PROVIDER_TYPES` is a table of types, and membership in
    it is membership in its keys.
    """
    if isinstance(node, ast.Call) and _call_name(node) in _CONTAINER_TYPES:
        return _string_members(node.args[0]) if node.args else ()
    if isinstance(node, ast.Dict):
        elements = list(node.keys)
    elif isinstance(node, ast.Tuple | ast.List | ast.Set):
        elements = list(node.elts)
    else:
        return None
    values = []
    for element in elements:
        if not isinstance(element, ast.Constant) or not isinstance(element.value, str):
            return None
        values.append(element.value)
    return tuple(sorted(values))


def collect_constant_sets(repo_root: Path) -> dict[str, tuple[str, ...] | None]:
    """Module-level collections of strings, by name, from everything under `app/`.

    A name two modules define differently is stored as None: unresolvable is the only
    honest answer, and a wrong value set would be worse than none.
    """
    table: dict[str, tuple[str, ...] | None] = {}
    for path in sorted((repo_root / "app").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if not isinstance(node, ast.Assign) or len(node.targets) != 1:
                continue
            target = node.targets[0]
            if not isinstance(target, ast.Name):
                continue
            members = _string_members(node.value)
            if members is None:
                continue
            if table.get(target.id, members) != members:
                table[target.id] = None
            else:
                table[target.id] = members
    return table


def collect_membership_predicates(
    repo_root: Path, constants: dict[str, tuple[str, ...] | None]
) -> dict[str, tuple[str, ...]]:
    """`def is_valid_colour(v): return v in _COLOUR_VALID` -> the set, by function name.

    One level deep and no further. It is what `TagIn.color` does, and without following it
    the tag palette would read as "any string" while the route replaces anything outside
    the set with `blue`.
    """
    predicates: dict[str, tuple[str, ...]] = {}
    for path in sorted((repo_root / "app").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef) or len(node.body) != 1:
                continue
            statement = node.body[0]
            if not isinstance(statement, ast.Return):
                continue
            members = _membership_target(statement.value, constants)
            if members:
                predicates[node.name] = members
    return predicates


def _range_target(node: ast.AST | None) -> tuple[int, int] | None:
    """The bounds of a chained `lo <= x <= hi`, which is how this codebase spells a range.

    `is_valid_port` returns one and `TemplateIn.valid_port` negates one; both mean a port is
    1 to 65535, and neither is visible to a check that reads annotations only.
    """
    if not isinstance(node, ast.Compare) or len(node.ops) != 2:
        return None
    low, high = node.left, node.comparators[1]
    if not (isinstance(low, ast.Constant) and isinstance(low.value, int)):
        return None
    if not (isinstance(high, ast.Constant) and isinstance(high.value, int)):
        return None
    if not all(isinstance(op, ast.Lt | ast.LtE) for op in node.ops):
        return None
    minimum = low.value + (0 if isinstance(node.ops[0], ast.LtE) else 1)
    maximum = high.value - (0 if isinstance(node.ops[1], ast.LtE) else 1)
    return minimum, maximum


def collect_range_predicates(repo_root: Path) -> dict[str, tuple[int, int]]:
    """`def is_valid_port(v): return 1 <= int(v) <= 65535` -> the bounds, by function name."""
    predicates: dict[str, tuple[int, int]] = {}
    for path in sorted((repo_root / "app").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            for child in ast.walk(node):
                if not isinstance(child, ast.Return):
                    continue
                bounds = _range_target(child.value)
                if bounds:
                    predicates[node.name] = bounds
                    break
    return predicates


def _membership_target(
    node: ast.AST | None, constants: dict[str, tuple[str, ...] | None]
) -> tuple[str, ...]:
    """The value set of an `x in <collection>` / `x not in <collection>` comparison."""
    if not isinstance(node, ast.Compare) or len(node.ops) != 1:
        return ()
    if not isinstance(node.ops[0], ast.In | ast.NotIn):
        return ()
    right = node.comparators[0]
    members = _string_members(right)
    if members is not None:
        return members
    if isinstance(right, ast.Name):
        return constants.get(right.id) or ()
    if isinstance(right, ast.Attribute):
        return constants.get(right.attr) or ()
    return ()


def _field_call_facts(node: ast.Call) -> tuple[bool, TypeFacts]:
    """What a `Field(...)` says: whether it carries a default, and the bounds it sets."""
    has_default = bool(node.args)
    bounds = TypeFacts()
    for keyword in node.keywords:
        if keyword.arg in ("default", "default_factory"):
            has_default = True
        if not isinstance(keyword.value, ast.Constant):
            continue
        value = keyword.value.value
        if keyword.arg in ("min_length", "min_items"):
            bounds = replace(bounds, min_items=value)
        elif keyword.arg == "ge":
            bounds = replace(bounds, minimum=value)
        elif keyword.arg == "gt":
            bounds = replace(bounds, minimum=value + 1)
        elif keyword.arg == "le":
            bounds = replace(bounds, maximum=value)
        elif keyword.arg == "lt":
            bounds = replace(bounds, maximum=value - 1)
    return has_default, bounds


def _with_bounds(facts: TypeFacts, bounds: TypeFacts) -> TypeFacts:
    """Lay a `Field(...)`'s bounds over an annotation, keeping what it does not mention."""
    return replace(
        facts,
        min_items=bounds.min_items if bounds.min_items is not None else facts.min_items,
        minimum=bounds.minimum if bounds.minimum is not None else facts.minimum,
        maximum=bounds.maximum if bounds.maximum is not None else facts.maximum,
    )


def _facts(node: ast.AST | None) -> TypeFacts:
    """Read an annotation. Unknown shapes answer `any`, which constrains nothing."""
    if node is None:
        return TypeFacts()
    if isinstance(node, ast.Constant):
        if node.value is None:
            return TypeFacts(base="none")
        if isinstance(node.value, str):
            # A stringified annotation. Parsing it is possible; nothing here uses one.
            return TypeFacts()
        return TypeFacts()
    if isinstance(node, ast.Name):
        return TypeFacts(base=node.id if node.id in _KNOWN_BASES else "any")
    if isinstance(node, ast.Attribute):
        return TypeFacts()
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        left, right = _facts(node.left), _facts(node.right)
        nullable = left.base == "none" or right.base == "none" or left.nullable or right.nullable
        carrier = right if left.base == "none" else left
        return replace(carrier, nullable=nullable)
    if isinstance(node, ast.Subscript):
        return _subscript_facts(node)
    return TypeFacts()


_KNOWN_BASES = {"str", "int", "float", "bool", "list", "dict", "set", "tuple", "none"}


def _subscript_facts(node: ast.Subscript) -> TypeFacts:
    head = node.value.id if isinstance(node.value, ast.Name) else ""
    arguments = list(node.slice.elts) if isinstance(node.slice, ast.Tuple) else [node.slice]

    if head == "Literal":
        values = tuple(sorted(
            a.value for a in arguments if isinstance(a, ast.Constant) and isinstance(a.value, str)
        ))
        return TypeFacts(base="str" if values else "any", enum=values)
    if head == "Optional":
        return replace(_facts(arguments[0]), nullable=True)
    if head == "Annotated":
        inner = _facts(arguments[0])
        for extra in arguments[1:]:
            if _call_name(extra) == "Field":
                _, bounds = _field_call_facts(extra)
                inner = _with_bounds(inner, bounds)
        return inner
    if head in ("list", "set", "tuple", "frozenset"):
        inner = _facts(arguments[0])
        return TypeFacts(base="list", item_enum=inner.enum)
    if head == "dict":
        return TypeFacts(base="dict")
    return TypeFacts()


# ---------------------------------------------------------------------------------------
# The API side: models, the value sets their validators impose, and the routes using them
# ---------------------------------------------------------------------------------------


@dataclass
class ModelField:
    name: str
    facts: TypeFacts
    required: bool
    enum: tuple[str, ...] = ()
    item_enum: tuple[str, ...] = ()


@dataclass
class ApiModel:
    name: str
    bases: tuple[str, ...]
    fields: dict[str, ModelField] = field(default_factory=dict)
    forbids_extra: bool = False
    resolved: bool = False


@dataclass
class ApiRoute:
    method: str
    path: str
    handler: str
    model: str | None = None
    body_is_dict: bool = False
    query_params: dict[str, bool] = field(default_factory=dict)  # name -> required
    enums: dict[str, tuple[str, ...]] = field(default_factory=dict)  # route-level value sets


def _model_from_class(
    node: ast.ClassDef,
    constants: dict[str, tuple[str, ...] | None],
    predicates: dict[str, tuple[str, ...]],
) -> ApiModel:
    bases = tuple(b.id for b in node.bases if isinstance(b, ast.Name))
    model = ApiModel(name=node.name, bases=bases)

    for statement in node.body:
        if isinstance(statement, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "model_config" for t in statement.targets
        ):
            model.forbids_extra = any(
                keyword.arg == "extra" and getattr(keyword.value, "value", "") == "forbid"
                for keyword in getattr(statement.value, "keywords", [])
            )
            continue

        if isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name):
            name = statement.target.id
            if name.startswith("model_config"):
                continue
            facts = _facts(statement.annotation)
            required = statement.value is None
            if isinstance(statement.value, ast.Call) and _call_name(statement.value) == "Field":
                has_default, bounds = _field_call_facts(statement.value)
                required = not has_default
                facts = _with_bounds(facts, bounds)
            model.fields[name] = ModelField(
                name=name, facts=facts, required=required,
                enum=facts.enum, item_enum=facts.item_enum,
            )
            continue

        if isinstance(statement, ast.FunctionDef):
            for name, found in _validator_facts(statement, constants, predicates).items():
                target = model.fields.get(name)
                if target is None:
                    continue
                if found.enum:
                    if target.facts.base == "list":
                        target.item_enum = target.item_enum or found.enum
                    else:
                        target.enum = target.enum or found.enum
                if found.minimum is not None or found.maximum is not None:
                    target.facts = _with_bounds(target.facts, found)

    return model


def _validator_facts(
    node: ast.FunctionDef,
    constants: dict[str, tuple[str, ...] | None],
    predicates: dict[str, tuple[str, ...]],
) -> dict[str, TypeFacts]:
    """What a `@field_validator` imposes, for each field it is registered on.

    Refusal and replacement count the same. `TagIn.color` swaps anything outside the
    palette for `blue` instead of raising, and a caller who asked for `chartreuse` and was
    given `blue` was not obeyed either.
    """
    fields: list[str] = []
    for decorator in node.decorator_list:
        if _call_name(decorator) != "field_validator":
            continue
        fields.extend(
            a.value for a in decorator.args if isinstance(a, ast.Constant) and isinstance(a.value, str)
        )
    if not fields:
        return {}

    found = TypeFacts()
    for child in ast.walk(node):
        if not found.enum:
            members = _membership_target(child, constants)
            if not members and isinstance(child, ast.Call) and _call_name(child) in predicates:
                members = predicates[_call_name(child)]
            if members:
                found = replace(found, enum=members)
        if found.minimum is None:
            bounds = _range_target(child)
            if not bounds and isinstance(child, ast.Call) and _call_name(child) in _RANGE_PREDICATES:
                bounds = _RANGE_PREDICATES[_call_name(child)]
            if bounds:
                found = replace(found, minimum=bounds[0], maximum=bounds[1])
    if not found.enum and found.minimum is None:
        return {}
    return dict.fromkeys(fields, found)


def collect_api_contracts(
    repo_root: Path,
) -> tuple[dict[str, ApiModel], dict[tuple[str, str], ApiRoute]]:
    """Every pydantic body model under `app/api/`, and the routes that validate against one."""
    constants = collect_constant_sets(repo_root)
    predicates = collect_membership_predicates(repo_root, constants)
    _RANGE_PREDICATES.clear()
    _RANGE_PREDICATES.update(collect_range_predicates(repo_root))

    models: dict[str, ApiModel] = {}
    routes: dict[tuple[str, str], ApiRoute] = {}

    for path in sorted((repo_root / "app" / "api").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                models[node.name] = _model_from_class(node, constants, predicates)
            elif isinstance(node, ast.FunctionDef):
                route = _route_from_function(node, models, constants)
                if route is not None:
                    routes[(route.method, route.path)] = route

    for name in list(models):
        _resolve_inheritance(name, models)
    return models, routes


def _resolve_inheritance(name: str, models: dict[str, ApiModel]) -> ApiModel:
    """Fold a parent's fields under a child's, so `ServicePreflightIn` carries `ServiceIn`."""
    model = models[name]
    if model.resolved:
        return model
    model.resolved = True
    merged: dict[str, ModelField] = {}
    for base in model.bases:
        if base in models:
            parent = _resolve_inheritance(base, models)
            merged.update(parent.fields)
            model.forbids_extra = model.forbids_extra or parent.forbids_extra
    merged.update(model.fields)
    model.fields = merged
    return model


def _route_from_function(
    node: ast.FunctionDef,
    models: dict[str, ApiModel],
    constants: dict[str, tuple[str, ...] | None],
) -> ApiRoute | None:
    decorator = next(
        (
            d for d in node.decorator_list
            if isinstance(d, ast.Call)
            and isinstance(d.func, ast.Attribute)
            and isinstance(d.func.value, ast.Name)
            and d.func.value.id == "router"
        ),
        None,
    )
    if decorator is None or not decorator.args:
        return None
    raw_path = decorator.args[0]
    if not isinstance(raw_path, ast.Constant):
        return None

    route = ApiRoute(
        method=decorator.func.attr.upper(),
        path=normalize(raw_path.value),
        handler=node.name,
    )

    path_names = set(re.findall(r"\{([^}]+)\}", raw_path.value))
    arguments = list(node.args.args) + list(node.args.kwonlyargs)
    defaults = [None] * (len(node.args.args) - len(node.args.defaults)) + list(node.args.defaults)
    defaults += list(node.args.kw_defaults)
    body_name = ""

    for argument, default in zip(arguments, defaults, strict=False):
        facts = _facts(argument.annotation)
        annotation_name = _annotation_root(argument.annotation)
        if annotation_name == "Request" or argument.arg in path_names:
            continue
        if annotation_name in models:
            route.model = annotation_name
            body_name = argument.arg
            continue
        if _call_name(default) == "Body" or (facts.base == "dict" and default is None):
            route.body_is_dict = True
            body_name = argument.arg
            continue
        route.query_params[argument.arg] = default is None

    if body_name:
        for child in ast.walk(node):
            if not isinstance(child, ast.Compare):
                continue
            left = child.left
            if (
                isinstance(left, ast.Attribute)
                and isinstance(left.value, ast.Name)
                and left.value.id == body_name
            ):
                members = _membership_target(child, constants)
                if members:
                    route.enums.setdefault(left.attr, members)
    return route


def _annotation_root(node: ast.AST | None) -> str:
    """The named type of an annotation, looking through `X | None`."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        return _annotation_root(node.left) or _annotation_root(node.right)
    return ""


# ---------------------------------------------------------------------------------------
# The bridge side: what each tool declares, and what it puts in the payload
# ---------------------------------------------------------------------------------------


@dataclass
class ToolParam:
    name: str
    facts: TypeFacts
    has_default: bool


@dataclass
class Sent:
    """Where one payload key gets its value."""

    kind: str            # param | cond-param | mask | const | expr
    source: str = ""     # the parameter name, for param / cond-param / mask
    detail: str = ""     # the fallback, or the expression, as written


@dataclass
class ToolCall:
    method: str
    path: str
    payload: dict[str, Sent] | None = None
    complete: bool = True   # False once a key can be absent, or the body comes from a helper
    derived: bool = False   # the body is a full one read back from the API and overlaid
    query_keys: tuple[str, ...] = ()


@dataclass
class McpTool:
    name: str
    module: str
    params: dict[str, ToolParam]
    calls: list[ToolCall]


def collect_mcp_contracts(repo_root: Path) -> dict[str, McpTool]:
    tools: dict[str, McpTool] = {}
    for path in sorted((repo_root / "vauxtra_mcp" / "tools").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and _is_tool(node):
                tools[node.name] = McpTool(
                    name=node.name,
                    module=path.name,
                    params=_tool_params(node),
                    calls=_tool_calls(node),
                )
    return tools


def _tool_params(node: ast.FunctionDef) -> dict[str, ToolParam]:
    params: dict[str, ToolParam] = {}
    positional = list(node.args.args)
    defaults = [None] * (len(positional) - len(node.args.defaults)) + list(node.args.defaults)
    for argument, default in zip(positional, defaults, strict=True):
        params[argument.arg] = ToolParam(
            name=argument.arg, facts=_facts(argument.annotation), has_default=default is not None
        )
    for argument, default in zip(node.args.kwonlyargs, node.args.kw_defaults, strict=True):
        params[argument.arg] = ToolParam(
            name=argument.arg, facts=_facts(argument.annotation), has_default=default is not None
        )
    return params


def _provenance(node: ast.AST, params: dict[str, ToolParam]) -> Sent:
    if isinstance(node, ast.Name) and node.id in params:
        return Sent(kind="param", source=node.id)
    if isinstance(node, ast.Constant):
        return Sent(kind="const", detail=repr(node.value))
    if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or):
        head = node.values[0]
        if isinstance(head, ast.Name) and head.id in params:
            fallback = " or ".join(ast.unparse(v) for v in node.values[1:])
            return Sent(kind="mask", source=head.id, detail=fallback)
    return Sent(kind="expr", detail=ast.unparse(node))


def _dict_payload(node: ast.Dict, params: dict[str, ToolParam]) -> tuple[dict[str, Sent], bool]:
    payload: dict[str, Sent] = {}
    complete = True
    for key, value in zip(node.keys, node.values, strict=True):
        if key is None:  # `**something`: the rest of the body comes from elsewhere
            complete = False
            continue
        if not isinstance(key, ast.Constant) or not isinstance(key.value, str):
            complete = False
            continue
        payload[key.value] = _provenance(value, params)
    return payload, complete


def _tool_calls(node: ast.FunctionDef) -> list[ToolCall]:
    params = _tool_params(node)
    bodies: dict[str, tuple[dict[str, Sent], bool, bool]] = {}  # name -> (payload, complete, derived)
    calls: list[ToolCall] = []

    for statement in ast.walk(node):
        # `payload = {...}` / `payload: dict[str, Any] = {...}`
        if isinstance(statement, ast.Assign | ast.AnnAssign):
            targets = statement.targets if isinstance(statement, ast.Assign) else [statement.target]
            value = statement.value
            for target in targets:
                if isinstance(target, ast.Name) and isinstance(value, ast.Dict):
                    payload, complete = _dict_payload(value, params)
                    bodies[target.id] = (payload, complete, not complete)
                elif (
                    isinstance(target, ast.Subscript)
                    and isinstance(target.value, ast.Name)
                    and target.value.id in bodies
                    and isinstance(target.slice, ast.Constant)
                ):
                    payload, _, derived = bodies[target.value.id]
                    payload[target.slice.value] = _provenance(value, params)
                    bodies[target.value.id] = (payload, False, derived)
                elif isinstance(target, ast.Name) and isinstance(value, ast.Call):
                    bodies[target.id] = ({}, False, True)

        # `for key, value in {...}.items(): ... payload[key] = value`
        if isinstance(statement, ast.For) and isinstance(statement.iter, ast.Call):
            iterated = statement.iter.func
            if (
                isinstance(iterated, ast.Attribute)
                and iterated.attr == "items"
                and isinstance(iterated.value, ast.Dict)
            ):
                overlay, _ = _dict_payload(iterated.value, params)
                written = {
                    sub.value.id
                    for child in ast.walk(statement)
                    if isinstance(child, ast.Assign)
                    for sub in child.targets
                    if isinstance(sub, ast.Subscript) and isinstance(sub.value, ast.Name)
                }
                for name in written & set(bodies):
                    payload, _, derived = bodies[name]
                    for key, sent in overlay.items():
                        payload[key] = Sent(kind="cond-param", source=sent.source, detail=sent.detail)
                    bodies[name] = (payload, False, derived)

    for statement in ast.walk(node):
        if not isinstance(statement, ast.Call):
            continue
        function = statement.func
        if not (
            isinstance(function, ast.Attribute)
            and isinstance(function.value, ast.Name)
            and function.value.id == "client"
            and function.attr in ("get", "post", "put", "delete", "patch")
        ):
            continue
        if not statement.args:
            continue
        path = _path_of(statement.args[0])
        if path is None:
            continue

        call = ToolCall(method=function.attr.upper(), path=path)
        for keyword in statement.keywords:
            if keyword.arg == "json":
                if isinstance(keyword.value, ast.Dict):
                    call.payload, call.complete = _dict_payload(keyword.value, params)
                    call.derived = not call.complete
                elif isinstance(keyword.value, ast.Name) and keyword.value.id in bodies:
                    call.payload, call.complete, call.derived = bodies[keyword.value.id]
                else:
                    call.payload, call.complete, call.derived = {}, False, True
            if keyword.arg == "params" and isinstance(keyword.value, ast.Dict):
                call.query_keys = tuple(
                    k.value for k in keyword.value.keys
                    if isinstance(k, ast.Constant) and isinstance(k.value, str)
                )
        calls.append(call)
    return calls


def _path_of(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return normalize(node.value)
    if isinstance(node, ast.JoinedStr):
        rendered = "".join(
            part.value if isinstance(part, ast.Constant) else "{}" for part in node.values
        )
        return normalize(rendered)
    return None


# ---------------------------------------------------------------------------------------
# The comparison
# ---------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Finding:
    tool: str
    target: str
    kind: str
    message: str

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.tool, self.target, self.kind)


def _values(members: tuple[str, ...]) -> str:
    return ", ".join(members)


def compare_contract(tool: McpTool, call: ToolCall, route: ApiRoute, model: ApiModel) -> list[Finding]:
    findings: list[Finding] = []
    where = f"{route.method} {route.path}"
    payload = call.payload or {}

    for name, spec in model.fields.items():
        enum = route.enums.get(name) or spec.enum
        item_enum = spec.item_enum
        sent = payload.get(name)

        if spec.required and not call.derived:
            if sent is None and call.complete:
                findings.append(Finding(
                    tool.name, name, "required-not-sent",
                    f"{tool.name}: {where} requires '{name}' and the tool never sends it",
                ))
            elif sent is not None and sent.kind == "mask":
                findings.append(Finding(
                    tool.name, name, "required-masked",
                    f"{tool.name}: '{name}' is required by {where}, and the tool falls back to "
                    f"`{sent.detail}` when '{sent.source}' is omitted -- it invents a value "
                    f"instead of refusing",
                ))
            elif sent is not None and sent.kind == "expr":
                findings.append(Finding(
                    tool.name, name, "required-from-expression",
                    f"{tool.name}: {where} requires '{name}' and no parameter carries it; the "
                    f"payload fills it with `{sent.detail}`",
                ))
            elif sent is not None and sent.kind in ("param", "cond-param"):
                param = tool.params[sent.source]
                if param.has_default or param.facts.nullable:
                    findings.append(Finding(
                        tool.name, sent.source, "required-declared-optional",
                        f"{tool.name}: parameter '{sent.source}' is optional "
                        f"({'has a default' if param.has_default else 'accepts None'}), but "
                        f"{where} requires '{name}'",
                    ))

        if sent is not None and sent.kind in ("param", "cond-param"):
            param = tool.params[sent.source]
            if not spec.required and not param.has_default and not call.derived:
                findings.append(Finding(
                    tool.name, sent.source, "optional-declared-required",
                    f"{tool.name}: parameter '{sent.source}' is required, but {where} accepts a "
                    f"body without '{name}'",
                ))
            if enum and param.facts.enum != enum:
                kind = "enum-mismatch" if param.facts.enum else "enum-not-declared"
                declared = _values(param.facts.enum) if param.facts.enum else param.facts.base
                findings.append(Finding(
                    tool.name, sent.source, kind,
                    f"{tool.name}: parameter '{sent.source}' declares {declared}, but {where} "
                    f"accepts only: {_values(enum)}",
                ))
            if item_enum and param.facts.item_enum != item_enum:
                kind = "enum-mismatch" if param.facts.item_enum else "enum-not-declared"
                findings.append(Finding(
                    tool.name, sent.source, kind,
                    f"{tool.name}: the items of '{sent.source}' are unconstrained, but {where} "
                    f"accepts only: {_values(item_enum)}",
                ))
            if (
                spec.facts.min_items is not None
                and param.facts.min_items != spec.facts.min_items
            ):
                findings.append(Finding(
                    tool.name, sent.source, "min-items-not-declared",
                    f"{tool.name}: parameter '{sent.source}' carries no minimum, but {where} "
                    f"demands at least {spec.facts.min_items} item(s) in '{name}'",
                ))
            if (spec.facts.minimum, spec.facts.maximum) != (None, None) and (
                param.facts.minimum,
                param.facts.maximum,
            ) != (spec.facts.minimum, spec.facts.maximum):
                findings.append(Finding(
                    tool.name, sent.source, "range-not-declared",
                    f"{tool.name}: parameter {sent.source!r} declares no bounds, but {where} "
                    f"accepts {name!r} only between {spec.facts.minimum} and "
                    f"{spec.facts.maximum}",
                ))
            if (
                spec.facts.base not in ("any", "none")
                and param.facts.base not in ("any", "none")
                and spec.facts.base != param.facts.base
            ):
                findings.append(Finding(
                    tool.name, sent.source, "type-mismatch",
                    f"{tool.name}: parameter '{sent.source}' is {param.facts.base}, but "
                    f"{where} reads '{name}' as {spec.facts.base}",
                ))

        if sent is not None and sent.kind == "const" and enum:
            literal = sent.detail.strip("'\"")
            if literal not in enum:
                findings.append(Finding(
                    tool.name, name, "enum-invalid-constant",
                    f"{tool.name}: sends '{name}'={sent.detail}, which {where} refuses; "
                    f"it accepts only: {_values(enum)}",
                ))

    for key in payload:
        if key in model.fields:
            continue
        how = "refuses it with a 422" if model.forbids_extra else "ignores it"
        findings.append(Finding(
            tool.name, key, "unknown-key",
            f"{tool.name}: sends '{key}', which {model.name} does not declare -- {where} {how}",
        ))

    for key in call.query_keys:
        if key not in route.query_params:
            findings.append(Finding(
                tool.name, key, "unknown-query-param",
                f"{tool.name}: passes the query parameter '{key}', which {where} does not read",
            ))

    return findings


def contract_findings(repo_root: Path) -> tuple[list[Finding], dict[str, int]]:
    models, routes = collect_api_contracts(repo_root)
    tools = collect_mcp_contracts(repo_root)

    findings: list[Finding] = []
    stats = {"tools": len(tools), "compared": 0, "dict_body": 0, "derived": 0}
    unmodelled: set[tuple[str, str]] = set()

    for tool in tools.values():
        for call in tool.calls:
            route = routes.get((call.method, call.path))
            if route is None or call.payload is None:
                continue
            if route.model is None:
                if route.body_is_dict:
                    unmodelled.add((route.method, route.path))
                continue
            stats["compared"] += 1
            if call.derived:
                stats["derived"] += 1
            findings.extend(compare_contract(tool, call, route, models[route.model]))

    stats["dict_body"] = len(unmodelled)
    stats["unmodelled_routes"] = sorted(unmodelled)
    return findings, stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--report",
        action="store_true",
        help="list every contract divergence, exempt ones included, and return 0",
    )
    options = parser.parse_args(argv)

    repo_root = Path(__file__).resolve().parents[1]
    api_routes = collect_api_routes(repo_root)
    mcp_routes = collect_mcp_routes(repo_root)

    api_only = sorted(api_routes - mcp_routes)
    mcp_only = sorted(mcp_routes - api_routes)
    covered = len(api_routes & mcp_routes)

    print(f"API_COUNT {len(api_routes)}")
    print(f"MCP_COUNT {len(mcp_routes)}")
    print(f"COVERED_COUNT {covered}")
    print(f"API_ONLY_COUNT {len(api_only)}")
    print(f"MCP_ONLY_COUNT {len(mcp_only)}")

    unexpected_api_only = [r for r in api_only if r not in ALLOWED_API_ONLY]
    if unexpected_api_only:
        print("Unexpected API-only routes:")
        for method, path in unexpected_api_only:
            print(f"{method} {path}")
        return 1

    stale = sorted(ALLOWED_API_ONLY - api_routes)
    if stale:
        # An allowlist entry for a route that no longer exists is an exemption nobody is
        # watching -- and the next route to land on that path inherits it.
        print("Allowlisted routes that no longer exist (remove them):")
        for method, path in stale:
            print(f"{method} {path}")
        return 1

    if mcp_only:
        print("MCP-only routes (non blocking):")
        for method, path in mcp_only:
            print(f"{method} {path}")

    tools = collect_mcp_tools(repo_root)
    documented = collect_documented_tools(repo_root)
    print(f"TOOL_COUNT {len(tools)}")
    print(f"DOCUMENTED_COUNT {len(documented)}")

    undocumented = sorted(tools - documented)
    if undocumented:
        print("Tools missing from vauxtra_mcp/README.md:")
        for name in undocumented:
            print(name)
        return 1

    phantom = sorted(documented - tools)
    if phantom:
        # A README that promises a tool is worse than one that omits it: the omission is
        # discovered by reading the code, the promise by calling something that is not there.
        print("Tools documented in vauxtra_mcp/README.md that do not exist:")
        for name in phantom:
            print(name)
        return 1

    findings, stats = contract_findings(repo_root)
    exempt = [f for f in findings if f.key in ALLOWED_CONTRACT_DIVERGENCES]
    divergent = [f for f in findings if f.key not in ALLOWED_CONTRACT_DIVERGENCES]

    print(f"CONTRACT_COMPARED_COUNT {stats['compared']}")
    print(f"CONTRACT_DERIVED_COUNT {stats['derived']}")
    print(f"CONTRACT_DIVERGENCE_COUNT {len(divergent)}")
    print(f"CONTRACT_EXEMPT_COUNT {len(exempt)}")
    print(f"UNMODELLED_BODY_ROUTE_COUNT {stats['dict_body']}")
    for method, path in stats["unmodelled_routes"]:
        # Not a failure: the route validates a plain dict by hand, so there is no contract
        # for a tool to declare. Printed so the blind spot stays visible.
        print(f"  no body model, nothing to compare: {method} {path}")

    if options.report:
        for finding in sorted(findings, key=lambda f: (f.tool, f.target, f.kind)):
            marker = "exempt " if finding.key in ALLOWED_CONTRACT_DIVERGENCES else "DIVERGE"
            print(f"[{marker}] {finding.kind}: {finding.message}")
        return 0

    if divergent:
        print("Tool signatures that do not match the model their route enforces:")
        for finding in sorted(divergent, key=lambda f: (f.tool, f.target, f.kind)):
            print(f"  {finding.kind}: {finding.message}")
        return 1

    observed = {f.key for f in findings}
    stale_exemptions = sorted(set(ALLOWED_CONTRACT_DIVERGENCES) - observed)
    if stale_exemptions:
        # Same reasoning as a stale route exemption: an allowance nobody is watching is one
        # the next signature inherits without ever having argued for it.
        print("Contract exemptions that no longer match anything (remove them):")
        for tool, target, kind in stale_exemptions:
            print(f"  {tool}.{target}: {kind}")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
