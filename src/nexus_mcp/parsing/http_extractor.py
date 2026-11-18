"""Routes and HTTP client calls in the source of one file (ADR-027, phase 2.3).

A route is a handler that a web framework serves at a method and a path. A client call is a
request that a function makes to a path. The service resolver of phase 2.4 matches the two
across repos. This module only finds them, with the same rule as `call_resolver`: precision
first. A path that is not a literal, or that does not start with `/` after normalising, is
left out.

Covered: Python (FastAPI, Starlette-style decorators, Flask, `requests`, `httpx`, `client.<verb>`,
`session.<verb>`), JavaScript and TypeScript (Express-style `app.get("/x", handler)`, NestJS
controllers, Next.js route files, `fetch`, `axios`, `ky`, `$fetch`). Not covered: Django
`path()`, Go, Java, C#, PHP, gRPC, a prefix that is set in another file, a path built by a
function, `requests.request("GET", url)`, a `url=` keyword, a prefix or a NestJS path that is not
a literal. A prefix set in another file leaves the route path relative: the resolver then
matches the end of the path.
"""

import re
from dataclasses import dataclass, field
from pathlib import PurePath
from typing import Dict, List, Optional, Tuple

HTTP_LANGUAGES = ("python", "javascript", "typescript")
MAX_HTTP_FACTS = 50  # per function; a file that registers more is a router table, not a handler

_VERBS = ("get", "post", "put", "patch", "delete", "head", "options")
_CLIENT_RECEIVERS_JS = ("axios", "ky")
_CLIENT_RECEIVERS_PY = ("requests", "httpx", "client", "session")
_NEST_VERBS = {v.capitalize(): v.upper() for v in _VERBS} | {"All": "ANY"}
_NEXT_VERBS = {v.upper() for v in _VERBS}

# A cheap test on the text, so that a file with no candidate is not walked.
_MAYBE = {
    "python": re.compile(r"\.(get|post|put|patch|delete|head|options|route|request)\s*\("),
    "javascript": re.compile(
        r"\b(fetch|axios|ky|\$fetch|ofetch)\b|\.(get|post|put|patch|delete|head|options|all)\s*\("
        r"|@(Get|Post|Put|Patch|Delete|Head|Options|All)\s*\(|export\s+(async\s+)?function\s+[A-Z]+"
    ),
}
_MAYBE["typescript"] = _MAYBE["javascript"]
# A route path or a URL starts a string with `/`, has `://`, or follows a `${...}` or `{...}`.
# A NestJS path has no leading slash (`@Get(":id")`), so a controller counts too.
_PATHISH = re.compile(r"[\"'`]/|://|\}/|@Controller\b")


@dataclass
class RouteFact:
    """A route found in a file. `def_line` (1-based) or `handler_name` finds the handler."""

    method: str
    path: str
    def_line: Optional[int] = None
    handler_name: Optional[str] = None


@dataclass
class CallFact:
    """A client call found in a file, at a 1-based line."""

    method: str
    path: str
    line: int


@dataclass
class HttpFacts:
    """Every route and client call of one file."""

    routes: List[RouteFact] = field(default_factory=list)
    calls: List[CallFact] = field(default_factory=list)


# --- paths --------------------------------------------------------------------------------

_SCHEME_HOST = re.compile(r"^(?:[A-Za-z][A-Za-z0-9+.\-]*:)?//[^/]*")
_TEMPLATE = re.compile(r"\$\{[^}]*\}")
_BRACES = re.compile(r"\{[^}]*\}")
_ANGLE = re.compile(r"<[^>/]*>")
_COLON_PARAM = re.compile(r"(?<=/):[A-Za-z_]\w*")
_BASE_VAR = re.compile(r"^(?:\{\})+(?=/)")


def _normalize_once(raw: str) -> str:
    text = _SCHEME_HOST.sub("", raw.strip(), count=1)
    text = _TEMPLATE.sub("{}", text)
    text = re.split(r"[?#]", text, maxsplit=1)[0]
    text = _BRACES.sub("{}", text)
    text = _ANGLE.sub("{}", text)
    text = _COLON_PARAM.sub("{}", text)
    text = _BASE_VAR.sub("", text)
    if not text.startswith("/"):
        return ""
    text = re.sub(r"/{2,}", "/", text).rstrip("/") or "/"
    # `/{}` or `/{}/{}` would match every route of that depth: no literal segment, no path.
    if text != "/" and not re.search(r"[^/{}]", text):
        return ""
    return text


def normalize_path(raw: str) -> str:
    """A route path or a client URL in one form, or "" when it is no path.

    The scheme, the host and a base-URL variable at the start are removed. Each parameter
    (`{id}`, `${id}`, `:id`, `<int:id>`) becomes `{}`. The query string and the fragment are
    removed, and so is a trailing slash. The result starts with `/`. Applying it again gives
    the same result.
    """
    text = raw
    for _ in range(3):  # the replacements can make a new match, so go to a fixed point
        after = _normalize_once(text)
        if after == text:
            break
        text = after
    return text if text.startswith("/") else ""


def _join(*parts: str) -> str:
    """Normalised path of the parts, joined with one slash between them. "" for no part."""
    if not any(parts):
        return ""
    return normalize_path("/" + "/".join(p.strip("/") for p in parts if p.strip("/")))


# --- literals -----------------------------------------------------------------------------

_QUOTED = re.compile(r"^[rbfuRBFU]{0,2}(\"\"\"|'''|\"|'|`)(.*)\1$", re.DOTALL)
_STRING_KINDS = ("string", "template_string", "concatenated_string")
_COMMENT_KINDS = ("comment",)


def _literal(node) -> Optional[str]:
    """The text of a string literal, or of a `+` of literals and other parts.

    A part that is not a literal becomes `{}`, so that `BASE + "/x/" + id` is `{}/x/{}`.
    Returns None when the node has no literal at all.
    """
    if node is None:
        return None
    kind = node.kind()
    if kind in _STRING_KINDS:
        match = _QUOTED.match(node.text().strip())
        return match.group(2) if match else None
    if kind in ("binary_operator", "binary_expression"):
        operator = node.field("operator")
        if operator is None or operator.text() != "+":
            return None
        left, right = node.field("left"), node.field("right")
        a, b = _literal(left), _literal(right)
        if a is None and b is None:
            return None
        return (a if a is not None else "{}") + (b if b is not None else "{}")
    if kind == "parenthesized_expression":
        inner = [c for c in node.children() if c.is_named()]
        return _literal(inner[0]) if len(inner) == 1 else None
    return None


def _positional(call) -> list:
    """The arguments of a call node that are not `name=value`."""
    args = call.field("arguments")
    if args is None:
        return []
    return [
        a
        for a in args.children()
        if a.is_named() and a.kind() not in ("keyword_argument", "comment", "list_splat")
    ]


def _keyword(call, name: str):
    """The value node of `name=value` in a Python call, or None."""
    args = call.field("arguments")
    if args is None:
        return None
    for a in args.children():
        if a.kind() == "keyword_argument":
            key = a.field("name")
            if key is not None and key.text() == name:
                return a.field("value")
    return None


def _object_value(obj, key: str):
    """The value node of `key: value` in a JavaScript object literal, or None."""
    if obj is None or obj.kind() != "object":
        return None
    for pair in obj.children():
        if pair.kind() != "pair":
            continue
        k = pair.field("key")
        if k is not None and k.text().strip("\"'`") == key:
            return pair.field("value")
    return None


def _line(node) -> int:
    return node.range().start.line + 1


# --- Python -------------------------------------------------------------------------------

_PY_ROUTER_FACTORIES = ("APIRouter", "Router", "Blueprint")
# A variable that a route decorator can belong to: made here by one of these, or named like one
# (an `app` or a `bp` that another file made). `@cache.get("/x")` is not a route.
_PY_APP_FACTORIES = _PY_ROUTER_FACTORIES + ("FastAPI", "Flask", "Starlette", "Sanic", "Quart")
_APP_NAME = re.compile(r"(?:^|_)(?:app|router|bp|blueprint)$", re.IGNORECASE)
_JS_APP_FACTORIES = (
    "express", "Router", "express.Router", "Fastify", "fastify", "Hono", "Koa", "KoaRouter",
    "Elysia", "Express",
)
# What the last argument of a route can be: a handler, not data.
_JS_HANDLER_KINDS = (
    "identifier", "member_expression", "arrow_function", "function_expression", "function",
    "call_expression", "await_expression",
)


def _py_app_vars(root) -> set:
    """Variables that a call of a known web-framework class assigns."""
    found = set()
    for assign in root.find_all(kind="assignment"):
        left, right = assign.field("left"), assign.field("right")
        if left is None or right is None or left.kind() != "identifier" or right.kind() != "call":
            continue
        factory = right.field("function")
        if factory is not None and factory.text().split(".")[-1] in _PY_APP_FACTORIES:
            found.add(left.text())
    return found


def _py_prefixes(root) -> Tuple[Dict[str, str], Dict[str, Tuple[str, str]]]:
    """(own prefix of each router variable, (parent, mount prefix) of each included router)."""
    own: Dict[str, str] = {}
    for assign in root.find_all(kind="assignment"):
        left, right = assign.field("left"), assign.field("right")
        if left is None or right is None or left.kind() != "identifier":
            continue
        if right.kind() != "call":
            continue
        factory = right.field("function")
        if factory is None or factory.text().split(".")[-1] not in _PY_ROUTER_FACTORIES:
            continue
        value = _keyword(right, "prefix") or _keyword(right, "url_prefix")
        own[left.text()] = _literal(value) or "" if value is not None else ""
    mounted: Dict[str, Tuple[str, str]] = {}
    for call in root.find_all(kind="call"):
        fn = call.field("function")
        if fn is None or fn.kind() != "attribute":
            continue
        if fn.field("attribute").text() not in ("include_router", "register_blueprint"):
            continue
        args = _positional(call)
        if not args or args[0].kind() != "identifier":
            continue
        value = _keyword(call, "prefix") or _keyword(call, "url_prefix")
        mounted[args[0].text()] = (
            fn.field("object").text(),
            (_literal(value) or "") if value is not None else "",
        )
    return own, mounted


def _py_full_prefix(var: str, own: Dict[str, str], mounted: Dict[str, Tuple[str, str]]) -> str:
    """The prefix of all routes of `var`: the prefixes of its parents, then its own."""
    parts = [own.get(var, "")]
    seen = {var}
    for _ in range(4):
        if var not in mounted:
            break
        parent, prefix = mounted[var]
        if parent in seen:
            break
        seen.add(parent)
        parts.append(prefix)
        parts.append(own.get(parent, ""))
        var = parent
    return "".join(reversed(parts))


def _py_methods(call, verb: str) -> List[str]:
    if verb != "route":
        return [verb.upper()]
    methods = _keyword(call, "methods")
    if methods is None:
        return ["GET"]
    found = [
        _literal(c).upper()
        for c in methods.children()
        if c.is_named() and _literal(c)
    ]
    return found or ["GET"]


def _py_routes(root, facts: HttpFacts) -> None:
    own, mounted = _py_prefixes(root)
    app_vars = _py_app_vars(root)
    for deco in root.find_all(kind="decorated_definition"):
        definition = deco.field("definition")
        if definition is None or definition.kind() != "function_definition":
            continue
        for decorator in deco.children():
            if decorator.kind() != "decorator":
                continue
            call = next((c for c in decorator.children() if c.kind() == "call"), None)
            fn = call.field("function") if call is not None else None
            if fn is None or fn.kind() != "attribute":
                continue
            verb = fn.field("attribute").text()
            receiver = fn.field("object")
            if receiver is None or receiver.kind() != "identifier":
                continue
            if receiver.text() not in app_vars and not _APP_NAME.search(receiver.text()):
                continue
            if verb not in _VERBS and verb != "route":
                continue
            args = _positional(call)
            raw = _literal(args[0]) if args else None
            if raw is None:
                continue
            prefix = _py_full_prefix(receiver.text(), own, mounted)
            path = _join(prefix, raw)
            if not path:
                continue
            for method in _py_methods(call, verb):
                facts.routes.append(RouteFact(method, path, def_line=_line(definition)))


def _py_calls(root, facts: HttpFacts) -> None:
    for call in root.find_all(kind="call"):
        fn = call.field("function")
        if fn is None or fn.kind() != "attribute":
            continue
        verb = fn.field("attribute").text()
        if verb not in _VERBS:
            continue
        receiver = fn.field("object").text().split(".")[-1]
        if receiver not in _CLIENT_RECEIVERS_PY:
            continue
        args = _positional(call)
        raw = _literal(args[0]) if args else None
        path = normalize_path(raw) if raw is not None else ""
        if path:
            facts.calls.append(CallFact(verb.upper(), path, _line(call)))


# --- JavaScript and TypeScript ------------------------------------------------------------


def _js_args(call) -> list:
    args = call.field("arguments")
    if args is None:
        return []
    return [a for a in args.children() if a.is_named() and a.kind() != "comment"]


def _js_mounts(root) -> Dict[str, Tuple[str, str]]:
    """(parent, prefix) of each router variable that `X.use("/p", router)` mounts."""
    mounted: Dict[str, Tuple[str, str]] = {}
    for call in root.find_all(kind="call_expression"):
        fn = call.field("function")
        if fn is None or fn.kind() != "member_expression":
            continue
        if fn.field("property").text() != "use":
            continue
        args = _js_args(call)
        if len(args) < 2 or args[1].kind() != "identifier":
            continue
        raw = _literal(args[0])
        if raw is not None and raw.startswith("/"):
            mounted[args[1].text()] = (fn.field("object").text(), raw)
    return mounted


def _js_prefix(var: str, mounted: Dict[str, Tuple[str, str]]) -> str:
    parts: List[str] = []
    seen = {var}
    for _ in range(4):
        if var not in mounted:
            break
        parent, prefix = mounted[var]
        if parent in seen:
            break
        seen.add(parent)
        parts.append(prefix)
        var = parent
    return "".join(reversed(parts))


def _js_handler_name(arg) -> Optional[str]:
    if arg.kind() == "identifier":
        return arg.text()
    if arg.kind() == "member_expression":
        return arg.field("property").text()
    return None


def _js_app_vars(root) -> set:
    """Variables that `express()`, `Router()`, `new Hono()` and the like assign."""
    found = set()
    for decl in root.find_all(kind="variable_declarator"):
        name, value = decl.field("name"), decl.field("value")
        if name is None or value is None or name.kind() != "identifier":
            continue
        if value.kind() == "call_expression":
            callee = value.field("function")
        elif value.kind() == "new_expression":
            callee = value.field("constructor")
        else:
            continue
        if callee is not None and callee.text() in _JS_APP_FACTORIES:
            found.add(name.text())
    return found


def _js_routes(root, facts: HttpFacts) -> None:
    mounted = _js_mounts(root)
    app_vars = _js_app_vars(root)
    for call in root.find_all(kind="call_expression"):
        fn = call.field("function")
        if fn is None or fn.kind() != "member_expression":
            continue
        verb = fn.field("property").text()
        receiver = fn.field("object")
        if verb not in _VERBS + ("all",) or receiver is None:
            continue
        if receiver.text() in _CLIENT_RECEIVERS_JS or receiver.kind() != "identifier":
            continue
        if receiver.text() not in app_vars and not _APP_NAME.search(receiver.text()):
            continue  # `api.post("/orders", body)` and `this.http.post(...)` are client calls
        args = _js_args(call)
        if len(args) < 2 or args[-1].kind() not in _JS_HANDLER_KINDS:
            continue  # `cache.get("key")` and `app.get("env")` have one argument
        raw = _literal(args[0])
        if raw is None or not raw.startswith("/"):
            continue
        path = _join(_js_prefix(receiver.text(), mounted), raw)
        if not path:
            continue
        method = "ANY" if verb == "all" else verb.upper()
        facts.routes.append(RouteFact(method, path, handler_name=_js_handler_name(args[-1])))


def _decorator_call(decorator):
    """(name, first literal argument or "") of a `@Name(...)` decorator.

    Returns None when it is no call, or when its first argument is not a literal: a path that
    the file does not give is left out (precision first)."""
    call = next((c for c in decorator.children() if c.kind() == "call_expression"), None)
    if call is None:
        return None
    fn = call.field("function")
    if fn is None or fn.kind() != "identifier":
        return None
    args = _js_args(call)
    if not args:
        return fn.text(), ""
    if args[0].kind() == "object":  # `@Controller({ path: "users" })`
        value = _object_value(args[0], "path")
        text = "" if value is None else _literal(value)
    else:
        text = _literal(args[0])
    return None if text is None else (fn.text(), text)


def _nest_routes(root, facts: HttpFacts) -> None:
    for cls in root.find_all(kind="class_declaration"):
        decorators = [c for c in cls.children() if c.kind() == "decorator"]
        parent = cls.parent()
        if parent is not None and parent.kind() == "export_statement":
            decorators += [c for c in parent.children() if c.kind() == "decorator"]
        prefix = None
        for d in decorators:
            found = _decorator_call(d)
            if found and found[0] == "Controller":
                prefix = found[1]
        if prefix is None:
            continue
        body = cls.field("body")
        if body is None:
            continue
        pending: list = []
        for child in body.children():
            if child.kind() == "decorator":
                pending.append(child)
                continue
            if child.kind() == "method_definition":
                name = child.field("name")
                for d in pending:
                    found = _decorator_call(d)
                    if not found or found[0] not in _NEST_VERBS:
                        continue
                    path = _join("/", prefix, found[1])
                    if path:
                        facts.routes.append(
                            RouteFact(
                                _NEST_VERBS[found[0]],
                                path,
                                def_line=_line(child),
                                handler_name=name.text() if name else None,
                            )
                        )
            pending = []


def _next_route_path(filepath: str) -> Optional[str]:
    """The URL path of a Next.js route file (`app/api/x/[id]/route.ts`), or None."""
    path = PurePath(filepath)
    if path.stem != "route" or path.suffix not in (".ts", ".js", ".tsx", ".jsx", ".mjs"):
        return None
    parts = path.parent.parts
    if "app" not in parts:
        return None
    segments = parts[len(parts) - 1 - parts[::-1].index("app") + 1 :]
    kept = [s for s in segments if not (s.startswith("(") and s.endswith(")"))]
    return normalize_path("/" + "/".join(re.sub(r"^\[\.{0,3}[^\]]*\]$", "{}", s) for s in kept))


def _next_routes(root, filepath: str, facts: HttpFacts) -> None:
    base = _next_route_path(filepath)
    if not base:
        return
    for export in root.find_all(kind="export_statement"):
        for child in export.children():
            if child.kind() == "function_declaration":
                name = child.field("name")
                if name is not None and name.text() in _NEXT_VERBS:
                    facts.routes.append(RouteFact(name.text(), base, def_line=_line(child)))
            elif child.kind() == "lexical_declaration":
                # `export const GET = async () => ...`: no function node, so the module gets it
                for decl in child.children():
                    name = decl.field("name") if decl.kind() == "variable_declarator" else None
                    if name is not None and name.text() in _NEXT_VERBS:
                        facts.routes.append(RouteFact(name.text(), base))


def _js_method(options) -> Optional[str]:
    """The method of a `fetch` or `axios` options object: GET when it has no `method` key,
    None when the method is not a literal (the call is left out)."""
    value = _object_value(options, "method")
    if value is None:
        return "GET"
    text = _literal(value)
    return text.upper() if text else None


def _js_calls(root, facts: HttpFacts) -> None:
    for call in root.find_all(kind="call_expression"):
        fn = call.field("function")
        if fn is not None and fn.kind() == "await_expression":
            # `await axios.get<User>("/u")` parses as a call of the await expression
            inner = [c for c in fn.children() if c.is_named()]
            fn = inner[0] if inner else None
        if fn is None:
            continue
        args = _js_args(call)
        method: Optional[str] = None
        url_node = None
        if fn.kind() == "identifier" and fn.text() in ("fetch", "$fetch", "ofetch"):
            if args:
                url_node, method = args[0], _js_method(args[1] if len(args) > 1 else None)
        elif fn.kind() == "identifier" and fn.text() in _CLIENT_RECEIVERS_JS:
            if args and args[0].kind() == "object":  # axios({ url, method })
                url_node, method = _object_value(args[0], "url"), _js_method(args[0])
            elif args:  # axios(url, { method })
                url_node, method = args[0], _js_method(args[1] if len(args) > 1 else None)
        elif fn.kind() == "member_expression" and fn.field("object").text() in _CLIENT_RECEIVERS_JS:
            verb = fn.field("property").text()
            if verb in _VERBS and args:
                url_node, method = args[0], verb.upper()
            elif verb == "request" and args and args[0].kind() == "object":
                url_node, method = _object_value(args[0], "url"), _js_method(args[0])
        raw = _literal(url_node) if url_node is not None else None
        path = normalize_path(raw) if raw is not None else ""
        if path and method:
            facts.calls.append(CallFact(method, path, _line(call)))


# --- entry point --------------------------------------------------------------------------


def extract_http(root, language: str, filepath: str, content: str) -> HttpFacts:
    """Routes and client calls of one file. `root` is the ast-grep root node.

    Returns empty facts for a language with no extractor, or for a file where a cheap text
    test finds no candidate.
    """
    facts = HttpFacts()
    if language not in HTTP_LANGUAGES:
        return facts
    next_file = _next_route_path(filepath) is not None
    if not next_file and not (
        _MAYBE[language].search(content) and _PATHISH.search(content)
    ):
        return facts
    if language == "python":
        _py_routes(root, facts)
        _py_calls(root, facts)
    else:
        _js_routes(root, facts)
        _nest_routes(root, facts)
        _next_routes(root, filepath, facts)
        _js_calls(root, facts)
    return facts
