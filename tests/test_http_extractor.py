"""Routes and HTTP client calls in the graph nodes (ADR-027, phase 2.3).

The parser is real; the files are real files in `tmp_path`. The extractor puts
`metadata["routes"]` on the handler function node and `metadata["http_calls"]` on the function
that makes the call. A route or a call outside any function node goes on the module node.
"""

import pytest
from hypothesis import given
from hypothesis import strategies as st

from nexus_mcp.core.graph_models import NodeType, UniversalGraph
from nexus_mcp.parsing.astgrep_parser import AstGrepParser
from nexus_mcp.parsing.http_extractor import normalize_path


def parse(tmp_path, name, source):
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source)
    graph = UniversalGraph()
    AstGrepParser().parse_file(str(path), graph)
    return graph


def func(graph, name):
    [node] = [n for n in graph.get_nodes_by_type(NodeType.FUNCTION) if n.name == name]
    return node


def module(graph):
    [node] = graph.get_nodes_by_type(NodeType.MODULE)
    return node


def pairs(entries):
    return [(e["method"], e["path"]) for e in entries]


# --- normalize_path -----------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("/users", "/users"),
        ("/users/", "/users"),
        ("/", "/"),
        ("https://api.example.com/v1/orders", "/v1/orders"),
        ("http://localhost:8000/health?verbose=1", "/health"),
        ("/orders/{order_id}", "/orders/{}"),
        ("/orders/${id}/items", "/orders/{}/items"),
        ("/users/:id", "/users/{}"),
        ("/users/:id/posts/:post", "/users/{}/posts/{}"),
        ("/files/<int:file_id>", "/files/{}"),
        ("/files/<name>", "/files/{}"),
        ("{BASE}/payments", "/payments"),
        ("${API_URL}/payments/${id}", "/payments/{}"),
        ("/a//b", "/a/b"),
        ("/search?q=a#top", "/search"),
    ],
)
def test_normalize_path_cases(raw, expected):
    assert normalize_path(raw) == expected


@pytest.mark.parametrize("raw", ["", "users", "env", "{}", "https://example.com", "mailto:a@b.c"])
def test_normalize_path_gives_empty_for_text_that_is_no_path(raw):
    assert normalize_path(raw) == ""


@given(st.text(alphabet="abc/{}:<>?#$.-_ ", max_size=40))
def test_normalize_path_is_stable_and_clean(raw):
    once = normalize_path(raw)
    assert normalize_path(once) == once
    assert once == "" or (once.startswith("/") and "?" not in once and "//" not in once)


# --- Python routes ------------------------------------------------------------------------


def test_fastapi_decorator_route_is_on_the_handler(tmp_path):
    graph = parse(
        tmp_path,
        "api.py",
        'from fastapi import FastAPI\napp = FastAPI()\n\n\n'
        '@app.get("/orders/{order_id}")\nasync def get_order(order_id: int) -> dict:\n'
        "    return {}\n\n\ndef helper():\n    return 1\n",
    )
    assert pairs(func(graph, "get_order").metadata["routes"]) == [("GET", "/orders/{}")]
    assert "routes" not in func(graph, "helper").metadata


def test_fastapi_all_verbs(tmp_path):
    src = "app = FastAPI()\n"
    for verb in ("get", "post", "put", "patch", "delete", "head", "options"):
        src += f'\n\n@app.{verb}("/r")\ndef {verb}_r():\n    pass\n'
    graph = parse(tmp_path, "api.py", src)
    seen = {func(graph, f"{v}_r").metadata["routes"][0]["method"] for v in
            ("get", "post", "put", "patch", "delete", "head", "options")}
    assert seen == {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}


def test_fastapi_router_prefix_in_the_same_file(tmp_path):
    graph = parse(
        tmp_path,
        "api.py",
        'router = APIRouter(prefix="/api/v1")\n\n\n@router.post("/orders")\n'
        "def create_order():\n    pass\n",
    )
    assert pairs(func(graph, "create_order").metadata["routes"]) == [("POST", "/api/v1/orders")]


def test_include_router_prefix_in_the_same_file(tmp_path):
    graph = parse(
        tmp_path,
        "api.py",
        'app = FastAPI()\nrouter = APIRouter(prefix="/orders")\n'
        'app.include_router(router, prefix="/api")\n\n\n@router.get("/{order_id}")\n'
        "def get_order(order_id):\n    pass\n",
    )
    assert pairs(func(graph, "get_order").metadata["routes"]) == [("GET", "/api/orders/{}")]


def test_flask_route_without_methods_is_get(tmp_path):
    graph = parse(tmp_path, "app.py", 'app = Flask(__name__)\n\n\n@app.route("/health")\n'
                  "def health():\n    return 'ok'\n")
    assert pairs(func(graph, "health").metadata["routes"]) == [("GET", "/health")]


def test_flask_route_with_methods_gives_one_route_for_each(tmp_path):
    graph = parse(
        tmp_path,
        "app.py",
        'app = Flask(__name__)\n\n\n@app.route("/items/<int:item_id>", methods=["GET", "PUT"])\n'
        "def item(item_id):\n    pass\n",
    )
    assert pairs(func(graph, "item").metadata["routes"]) == [
        ("GET", "/items/{}"),
        ("PUT", "/items/{}"),
    ]


def test_flask_blueprint_url_prefix_and_register_prefix(tmp_path):
    graph = parse(
        tmp_path,
        "app.py",
        'bp = Blueprint("orders", __name__, url_prefix="/orders")\n'
        'app.register_blueprint(bp, url_prefix="/v2")\n\n\n@bp.route("/list")\n'
        "def listing():\n    pass\n",
    )
    assert pairs(func(graph, "listing").metadata["routes"]) == [("GET", "/v2/orders/list")]


def test_a_decorator_that_is_no_route_adds_nothing(tmp_path):
    graph = parse(
        tmp_path,
        "app.py",
        'import functools\n\n\n@functools.lru_cache(maxsize=1)\ndef cached():\n    pass\n\n\n'
        '@app.get(some_variable)\ndef dynamic():\n    pass\n',
    )
    assert "routes" not in func(graph, "cached").metadata
    assert "routes" not in func(graph, "dynamic").metadata


# --- JavaScript and TypeScript routes -----------------------------------------------------


def test_express_route_with_a_named_handler(tmp_path):
    graph = parse(
        tmp_path,
        "server.js",
        "const app = express();\n\nfunction getUser(req, res) { res.json({}); }\n\n"
        'app.get("/users/:id", getUser);\n',
    )
    assert pairs(func(graph, "getUser").metadata["routes"]) == [("GET", "/users/{}")]


def test_express_route_with_an_inline_handler_goes_on_the_module(tmp_path):
    graph = parse(
        tmp_path,
        "server.js",
        'const app = express();\napp.post("/orders", async (req, res) => { res.send("ok"); });\n',
    )
    assert pairs(module(graph).metadata["routes"]) == [("POST", "/orders")]


def test_express_router_use_prefix_in_the_same_file(tmp_path):
    graph = parse(
        tmp_path,
        "server.js",
        "const app = express();\nconst router = express.Router();\n"
        'app.use("/api", router);\n\nfunction list(req, res) {}\n\nrouter.get("/items", list);\n',
    )
    assert pairs(func(graph, "list").metadata["routes"]) == [("GET", "/api/items")]


def test_a_get_call_that_is_no_route_adds_nothing(tmp_path):
    graph = parse(
        tmp_path,
        "util.js",
        'const x = cache.get("key");\nconst h = req.get("host");\nconst e = app.get("env");\n',
    )
    assert "routes" not in module(graph).metadata


def test_nest_controller_routes(tmp_path):
    graph = parse(
        tmp_path,
        "users.controller.ts",
        '@Controller("users")\nexport class UsersController {\n'
        '  @Get(":id")\n  findOne(id: string) { return id; }\n\n'
        "  @Post()\n  create() { return 1; }\n}\n",
    )
    assert pairs(func(graph, "findOne").metadata["routes"]) == [("GET", "/users/{}")]
    assert pairs(func(graph, "create").metadata["routes"]) == [("POST", "/users")]


def test_next_route_file_exports_are_routes(tmp_path):
    graph = parse(
        tmp_path,
        "app/api/orders/[id]/route.ts",
        "export async function GET(req: Request) { return Response.json({}); }\n\n"
        "export async function POST(req: Request) { return Response.json({}); }\n\n"
        "function helper() { return 1; }\n",
    )
    assert pairs(func(graph, "GET").metadata["routes"]) == [("GET", "/api/orders/{}")]
    assert pairs(func(graph, "POST").metadata["routes"]) == [("POST", "/api/orders/{}")]
    assert "routes" not in func(graph, "helper").metadata


# --- client calls -------------------------------------------------------------------------


def test_fetch_defaults_to_get_and_reads_the_method(tmp_path):
    graph = parse(
        tmp_path,
        "client.js",
        'function load() { return fetch("/api/orders"); }\n\n'
        "function save(order) {\n"
        '  return fetch(`${API}/api/orders/${order.id}`, { method: "PUT", body: "{}" });\n}\n',
    )
    assert pairs(func(graph, "load").metadata["http_calls"]) == [("GET", "/api/orders")]
    assert pairs(func(graph, "save").metadata["http_calls"]) == [("PUT", "/api/orders/{}")]


def test_axios_ky_and_nuxt_fetch_calls(tmp_path):
    graph = parse(
        tmp_path,
        "client.ts",
        'function a() { return axios.post("/p/a", {}); }\n'
        'function b() { return ky.delete("/p/b"); }\n'
        'function c() { return $fetch("/p/c", { method: "PATCH" }); }\n'
        'function d() { return axios({ url: "/p/d", method: "put" }); }\n',
    )
    assert pairs(func(graph, "a").metadata["http_calls"]) == [("POST", "/p/a")]
    assert pairs(func(graph, "b").metadata["http_calls"]) == [("DELETE", "/p/b")]
    assert pairs(func(graph, "c").metadata["http_calls"]) == [("PATCH", "/p/c")]
    assert pairs(func(graph, "d").metadata["http_calls"]) == [("PUT", "/p/d")]


def test_a_call_in_an_arrow_function_goes_on_the_module(tmp_path):
    graph = parse(
        tmp_path,
        "client.js",
        'export const load = async () => { const r = await fetch("/api/me"); return r; };\n',
    )
    assert pairs(module(graph).metadata["http_calls"]) == [("GET", "/api/me")]


def test_a_call_with_no_path_is_left_out(tmp_path):
    graph = parse(
        tmp_path,
        "client.js",
        "function a(url) { return fetch(url); }\n"
        'function b() { return fetch("https://example.com"); }\n'
        'function c(m) { return m.get("key"); }\n',
    )
    for name in ("a", "b", "c"):
        assert "http_calls" not in func(graph, name).metadata


def test_python_requests_httpx_client_and_session_calls(tmp_path):
    graph = parse(
        tmp_path,
        "client.py",
        "import requests\nimport httpx\n\n\n"
        'def a(order_id):\n    return requests.get(f"{BASE}/orders/{order_id}")\n\n\n'
        'def b():\n    return httpx.post("https://pay.example.com/charge", json={})\n\n\n'
        'def c(self):\n    return self.client.delete("/orders/1")\n\n\n'
        'def d(session, pid):\n    return session.put("/items/" + pid)\n',
    )
    assert pairs(func(graph, "a").metadata["http_calls"]) == [("GET", "/orders/{}")]
    assert pairs(func(graph, "b").metadata["http_calls"]) == [("POST", "/charge")]
    assert pairs(func(graph, "c").metadata["http_calls"]) == [("DELETE", "/orders/1")]
    assert pairs(func(graph, "d").metadata["http_calls"]) == [("PUT", "/items/{}")]


def test_a_call_goes_on_the_inner_function(tmp_path):
    graph = parse(
        tmp_path,
        "client.py",
        'def outer():\n    def inner():\n        return requests.get("/inner")\n    return inner\n',
    )
    assert pairs(func(graph, "inner").metadata["http_calls"]) == [("GET", "/inner")]
    assert "http_calls" not in func(graph, "outer").metadata


def test_a_dict_get_in_python_is_no_client_call(tmp_path):
    source = 'def f(d):\n    return d.get("/path")\n\n\ndef g(m):\n    return m.get("x")\n'
    graph = parse(tmp_path, "util.py", source)
    assert "http_calls" not in func(graph, "f").metadata
    assert "http_calls" not in func(graph, "g").metadata


def test_the_same_call_twice_is_listed_once(tmp_path):
    graph = parse(
        tmp_path,
        "client.py",
        'def f():\n    requests.get("/a")\n    requests.get("/a")\n    requests.get("/b")\n',
    )
    assert pairs(func(graph, "f").metadata["http_calls"]) == [("GET", "/a"), ("GET", "/b")]


def test_a_file_with_no_route_and_no_call_has_no_http_metadata(tmp_path):
    graph = parse(tmp_path, "plain.py", "def add(a, b):\n    return a + b\n")
    assert "routes" not in func(graph, "add").metadata
    assert "http_calls" not in func(graph, "add").metadata
    assert "routes" not in module(graph).metadata
    assert "http_calls" not in module(graph).metadata


def test_a_language_with_no_extractor_is_unchanged(tmp_path):
    graph = parse(tmp_path, "main.go", 'package main\n\nfunc main() {\n\thttp.Get("/x")\n}\n')
    assert "http_calls" not in func(graph, "main").metadata


# --- more cases: files, verbs, prefixes, limits (coverage audit) --------------------------


@pytest.mark.parametrize("name", ["view.tsx", "view.jsx"])
def test_tsx_and_jsx_files_give_calls_and_routes(tmp_path, name):
    graph = parse(
        tmp_path,
        name,
        'function Page() { return fetch("/api/page"); }\n'
        "function h() {}\n"
        'app.get("/x", h);\n',
    )
    assert pairs(func(graph, "Page").metadata["http_calls"]) == [("GET", "/api/page")]
    assert pairs(func(graph, "h").metadata["routes"]) == [("GET", "/x")]


def test_axios_request_with_an_object_reads_url_and_method(tmp_path):
    graph = parse(
        tmp_path,
        "client.js",
        'function a() { return axios.request({ url: "/r", method: "post" }); }\n'
        'function b() { return axios.request({ url: "/r2" }); }\n'
        'function c() { return axios.request("/r3"); }\n',
    )
    assert pairs(func(graph, "a").metadata["http_calls"]) == [("POST", "/r")]
    assert pairs(func(graph, "b").metadata["http_calls"]) == [("GET", "/r2")]
    assert "http_calls" not in func(graph, "c").metadata  # a string argument is not read


def test_axios_with_url_and_options_and_head(tmp_path):
    graph = parse(
        tmp_path,
        "client.ts",
        'function a() { return axios("/i", { method: "DELETE" }); }\n'
        'function b() { return axios.head("/h"); }\n'
        'function c() { return fetch("/g", { method: "post" }); }\n',
    )
    assert pairs(func(graph, "a").metadata["http_calls"]) == [("DELETE", "/i")]
    assert pairs(func(graph, "b").metadata["http_calls"]) == [("HEAD", "/h")]
    assert pairs(func(graph, "c").metadata["http_calls"]) == [("POST", "/g")]


def test_a_concatenated_and_a_template_url_keep_the_literal_parts(tmp_path):
    graph = parse(
        tmp_path,
        "client.ts",
        'function f() { return axios.get(`${BASE}/u/${id}`); }\n'
        'function g() { return fetch("/a" + id + "/b"); }\n'
        "function h() { return fetch(`/t`); }\n",
    )
    assert pairs(func(graph, "f").metadata["http_calls"]) == [("GET", "/u/{}")]
    assert pairs(func(graph, "g").metadata["http_calls"]) == [("GET", "/a{}/b")]
    assert pairs(func(graph, "h").metadata["http_calls"]) == [("GET", "/t")]


def test_express_all_is_any_and_a_middleware_before_the_handler_is_skipped(tmp_path):
    graph = parse(
        tmp_path,
        "server.js",
        "const app = express();\nfunction h() {}\nfunction auth() {}\n"
        'app.all("/any", h);\napp.get("/a", auth, h);\n',
    )
    assert pairs(func(graph, "h").metadata["routes"]) == [("ANY", "/any"), ("GET", "/a")]
    assert "routes" not in func(graph, "auth").metadata


def test_express_path_that_is_no_literal_or_has_no_slash_adds_nothing(tmp_path):
    graph = parse(
        tmp_path,
        "server.js",
        'const app = express();\nfunction h() {}\napp.get(path, h);\napp.get("nope", h);\n'
        'app.use("/x", handlerFn);\n',
    )
    assert "routes" not in func(graph, "h").metadata
    assert "routes" not in module(graph).metadata


def test_express_member_handler_that_is_not_a_node_goes_on_the_module(tmp_path):
    graph = parse(tmp_path, "server.js", 'app.get("/m", ctrl.list);\n')
    assert pairs(module(graph).metadata["routes"]) == [("GET", "/m")]


def test_express_nested_routers_join_every_prefix(tmp_path):
    graph = parse(
        tmp_path,
        "server.js",
        "const app = express();\nconst a = express.Router();\nconst b = express.Router();\n"
        'function h() {}\napp.use("/a", a);\na.use("/b", b);\nb.get("/c", h);\n',
    )
    assert pairs(func(graph, "h").metadata["routes"]) == [("GET", "/a/b/c")]


def test_express_router_loop_ends(tmp_path):
    graph = parse(
        tmp_path,
        "server.js",
        "const a = express.Router();\nconst b = express.Router();\nfunction h() {}\n"
        'a.use("/a", b);\nb.use("/b", a);\na.get("/c", h);\n',
    )
    [route] = func(graph, "h").metadata["routes"]
    assert route["method"] == "GET" and route["path"].endswith("/c")


def test_python_nested_routers_join_every_prefix(tmp_path):
    graph = parse(
        tmp_path,
        "api.py",
        'app = FastAPI()\nouter = APIRouter(prefix="/v1")\ninner = APIRouter(prefix="/orders")\n'
        'app.include_router(outer, prefix="/api")\nouter.include_router(inner)\n\n\n'
        '@inner.get("/{order_id}")\ndef get_order(order_id):\n    pass\n',
    )
    assert pairs(func(graph, "get_order").metadata["routes"]) == [
        ("GET", "/api/v1/orders/{}")
    ]


def test_python_router_loop_ends(tmp_path):
    graph = parse(
        tmp_path,
        "api.py",
        'a = APIRouter(prefix="/a")\nb = APIRouter(prefix="/b")\n'
        'a.include_router(b)\nb.include_router(a)\n\n\n@a.get("/c")\ndef f():\n    pass\n',
    )
    [route] = func(graph, "f").metadata["routes"]
    assert route["path"].endswith("/c")


def test_include_router_of_an_unknown_variable_keeps_the_own_prefix(tmp_path):
    graph = parse(
        tmp_path,
        "api.py",
        'app = FastAPI()\napp.include_router(imported_router, prefix="/x")\n'
        'router = APIRouter(prefix="/p")\n\n\n@router.get("/q")\ndef f():\n    pass\n',
    )
    assert pairs(func(graph, "f").metadata["routes"]) == [("GET", "/p/q")]


def test_python_route_on_a_class_method_is_on_the_method(tmp_path):
    graph = parse(
        tmp_path,
        "api.py",
        'class Views:\n    @app.get("/m")\n    def meth(self):\n        pass\n\n'
        '    @app.get("/n")\n    async def ameth(self):\n        pass\n',
    )
    assert pairs(func(graph, "meth").metadata["routes"]) == [("GET", "/m")]
    assert pairs(func(graph, "ameth").metadata["routes"]) == [("GET", "/n")]


def test_python_stacked_route_decorators_give_all_routes_once(tmp_path):
    graph = parse(
        tmp_path,
        "api.py",
        '@app.get("/a")\n@app.post("/a")\n@app.get("/a")\ndef f():\n    pass\n',
    )
    assert pairs(func(graph, "f").metadata["routes"]) == [("GET", "/a"), ("POST", "/a")]


def test_python_fstring_with_only_a_variable_is_no_call(tmp_path):
    graph = parse(
        tmp_path,
        "client.py",
        'def a(i):\n    return requests.get(f"{i}")\n\n\n'
        'def b(i):\n    return requests.get(f"/o/{i}/x")\n\n\n'
        'def c(i):\n    return requests.get(f"{BASE}/o/{i}")\n',
    )
    assert "http_calls" not in func(graph, "a").metadata
    assert pairs(func(graph, "b").metadata["http_calls"]) == [("GET", "/o/{}/x")]
    assert pairs(func(graph, "c").metadata["http_calls"]) == [("GET", "/o/{}")]


def test_python_url_by_keyword_and_requests_request_are_not_read(tmp_path):
    graph = parse(
        tmp_path,
        "client.py",
        'def f():\n    requests.request("GET", "/a")\n    requests.get(url="/k")\n',
    )
    assert "http_calls" not in func(graph, "f").metadata


def test_python_file_with_a_syntax_error_does_not_raise(tmp_path):
    graph = parse(
        tmp_path,
        "broken.py",
        'def f(:\n    requests.get("/a")\n\n\n@app.get("/z")\ndef g():\n    pass\n',
    )
    assert graph.get_nodes_by_type(NodeType.MODULE)  # the file is still in the graph


def test_js_file_with_a_syntax_error_does_not_raise(tmp_path):
    graph = parse(tmp_path, "broken.js", 'function f( {\n  fetch("/a")\n')
    assert graph.get_nodes_by_type(NodeType.MODULE)


def test_calls_over_the_limit_are_cut_to_max_http_facts(tmp_path):
    from nexus_mcp.parsing.http_extractor import MAX_HTTP_FACTS

    src = "def f():\n" + "".join(f'    requests.get("/p{i}")\n' for i in range(MAX_HTTP_FACTS + 10))
    graph = parse(tmp_path, "client.py", src)
    calls = func(graph, "f").metadata["http_calls"]
    assert len(calls) == MAX_HTTP_FACTS
    assert calls[0]["path"] == "/p0"


def test_routes_over_the_limit_are_cut_to_max_http_facts(tmp_path):
    from nexus_mcp.parsing.http_extractor import MAX_HTTP_FACTS

    src = "function h() {}\n" + "".join(
        f'app.get("/r{i}", h);\n' for i in range(MAX_HTTP_FACTS + 5)
    )
    graph = parse(tmp_path, "server.js", src)
    assert len(func(graph, "h").metadata["routes"]) == MAX_HTTP_FACTS


def test_nest_all_and_empty_controller_prefix(tmp_path):
    graph = parse(
        tmp_path,
        "a.controller.ts",
        '@Controller()\nexport class A {\n  @All("x")\n  any() {}\n\n  @Get()\n  root() {}\n}\n',
    )
    assert pairs(func(graph, "any").metadata["routes"]) == [("ANY", "/x")]
    assert pairs(func(graph, "root").metadata["routes"]) == [("GET", "/")]


def test_nest_decorator_with_a_non_literal_path_adds_no_route(tmp_path):
    graph = parse(
        tmp_path,
        "a.controller.ts",
        '@Controller("users")\nexport class A {\n  @Get(dynamic)\n  d() {}\n}\n',
    )
    assert "routes" not in func(graph, "d").metadata


def test_next_route_groups_catch_all_and_src_app(tmp_path):
    graph = parse(
        tmp_path,
        "src/app/(grp)/api/[...slug]/route.ts",
        "export async function DELETE() { return 1; }\n",
    )
    assert pairs(func(graph, "DELETE").metadata["routes"]) == [("DELETE", "/api/{}")]


def test_next_route_at_the_app_root_is_slash_and_tsx_is_read(tmp_path):
    root = parse(tmp_path, "app/route.ts", "export function GET() {}\n")
    assert pairs(func(root, "GET").metadata["routes"]) == [("GET", "/")]
    tsx = parse(tmp_path, "app/api/x/route.tsx", "export function PUT() {}\n")
    assert pairs(func(tsx, "PUT").metadata["routes"]) == [("PUT", "/api/x")]


def test_route_file_outside_an_app_folder_is_no_next_route(tmp_path):
    graph = parse(tmp_path, "lib/route.ts", "export function GET() {}\n")
    assert "routes" not in func(graph, "GET").metadata


def test_next_route_exported_as_an_arrow_function_goes_on_the_module(tmp_path):
    """`export const GET = ...` is a common Next.js form. It has no function node."""
    graph = parse(tmp_path, "app/api/y/route.ts", "export const GET = async () => 1;\n")
    assert pairs(module(graph).metadata["routes"]) == [("GET", "/api/y")]


def test_a_route_with_a_def_line_and_a_clashing_name_still_finds_the_handler(tmp_path):
    graph = parse(
        tmp_path,
        "api.py",
        'def f():\n    pass\n\n\ndef f():\n    pass\n\n\n@app.get("/d")\ndef f():\n    pass\n',
    )
    [node] = [
        n for n in graph.get_nodes_by_type(NodeType.FUNCTION) if "routes" in n.metadata
    ]
    assert node.location.start_line == 10  # the third def, not the first or second


def test_extract_http_with_a_text_that_has_no_candidate_is_empty():
    from nexus_mcp.parsing.http_extractor import extract_http

    facts = extract_http(None, "python", "x.py", "def f():\n    return 1\n")
    assert facts.routes == [] and facts.calls == []
    assert extract_http(None, "go", "x.go", 'http.Get("/x")').calls == []


# --- fixes from the audit of phase 2.3 ------------------------------------------------------


def test_a_client_instance_call_with_a_body_is_no_route(tmp_path):
    """`api.post("/orders", body)` has two arguments but is a client call, not a route."""
    graph = parse(
        tmp_path,
        "client.ts",
        "class Svc {\n  create(o) { return this.http.post('/api/orders', o); }\n"
        "  find(m) { return m.get('/a', 1); }\n}\n"
        "function send(o) { return api.post('/orders', o); }\n"
        "function ask() { return client.get('/users', { params: {} }); }\n"
        "function hit() { return $.get('/old', cb); }\n",
    )
    for fn in ("create", "find", "send", "ask", "hit"):
        assert "routes" not in func(graph, fn).metadata
    assert "routes" not in module(graph).metadata


def test_an_express_receiver_from_a_known_factory_is_a_router(tmp_path):
    graph = parse(
        tmp_path,
        "srv.ts",
        "const app = new Hono();\nconst r = express.Router();\nconst f = Fastify();\n"
        "function a(c) {}\nfunction b(q, s) {}\nfunction c2(q, s) {}\n"
        'app.get("/h", a);\nr.post("/p", b);\nf.put("/f", c2);\n',
    )
    assert pairs(func(graph, "a").metadata["routes"]) == [("GET", "/h")]
    assert pairs(func(graph, "b").metadata["routes"]) == [("POST", "/p")]
    assert pairs(func(graph, "c2").metadata["routes"]) == [("PUT", "/f")]


def test_a_router_named_app_or_router_from_another_file_is_a_router(tmp_path):
    graph = parse(
        tmp_path,
        "routes.js",
        "function list(req, res) {}\nrouter.get('/items', list);\n"
        "function make(req, res) {}\napp.post('/items', make);\n",
    )
    assert pairs(func(graph, "list").metadata["routes"]) == [("GET", "/items")]
    assert pairs(func(graph, "make").metadata["routes"]) == [("POST", "/items")]


def test_a_route_whose_last_argument_is_data_is_no_route(tmp_path):
    graph = parse(tmp_path, "srv.js", "const app = express();\napp.get('/a', { x: 1 });\n")
    assert "routes" not in module(graph).metadata


def test_nest_controller_with_an_object_argument_keeps_its_path(tmp_path):
    graph = parse(
        tmp_path,
        "u.controller.ts",
        '@Controller({ path: "users", version: "1" })\nexport class U {\n'
        '  @Get(":id")\n  one() {}\n}\n',
    )
    assert pairs(func(graph, "one").metadata["routes"]) == [("GET", "/users/{}")]


def test_nest_constant_path_is_left_out_and_so_is_a_controller_with_a_constant(tmp_path):
    graph = parse(
        tmp_path,
        "u.controller.ts",
        '@Controller("u")\nexport class U {\n  @Get(SOME_CONST)\n  a() {}\n}\n\n'
        "@Controller(PREFIX)\nexport class V {\n  @Get('x')\n  b() {}\n}\n",
    )
    assert "routes" not in func(graph, "a").metadata
    assert "routes" not in func(graph, "b").metadata


def test_a_typed_awaited_axios_call_is_a_client_call(tmp_path):
    graph = parse(
        tmp_path,
        "client.ts",
        "async function load() {\n  const r = await axios.get<User>('/api/users');\n"
        "  return r;\n}\n"
        "async function save(u) { return await axios.post<User>('/api/users', u); }\n",
    )
    assert pairs(func(graph, "load").metadata["http_calls"]) == [("GET", "/api/users")]
    assert pairs(func(graph, "save").metadata["http_calls"]) == [("POST", "/api/users")]


@pytest.mark.parametrize(
    "raw",
    ["/{}", "{BASE}/{id}", "/{a}/{b}", "{a}/{b}"],
)
def test_normalize_path_leaves_out_a_path_of_parameters(raw):
    assert normalize_path(raw) == ""


def test_normalize_path_removes_the_host_of_a_protocol_relative_url():
    """It is a URL on another host, like `https://host/x`: the host is not part of the path."""
    assert normalize_path("//cdn.example.com/x.js") == "/x.js"


def test_a_url_made_of_a_base_and_a_parameter_is_no_client_call(tmp_path):
    graph = parse(
        tmp_path,
        "client.py",
        'def a(i):\n    return requests.get(f"{BASE}/{i}")\n\n\n'
        'def b(i):\n    return requests.get(BASE + "/" + i)\n',
    )
    assert "http_calls" not in func(graph, "a").metadata
    assert "http_calls" not in func(graph, "b").metadata


def test_a_blueprint_route_without_a_leading_slash_joins_with_one_slash(tmp_path):
    graph = parse(
        tmp_path,
        "app.py",
        'bp = Blueprint("x", __name__, url_prefix="/api")\n\n\n@bp.route("items")\n'
        "def items():\n    pass\n",
    )
    assert pairs(func(graph, "items").metadata["routes"]) == [("GET", "/api/items")]


def test_a_decorator_on_an_object_that_is_no_router_is_no_route(tmp_path):
    graph = parse(
        tmp_path,
        "util.py",
        'cache = TTLCache()\n\n\n@cache.get("/x")\ndef f():\n    pass\n',
    )
    assert "routes" not in func(graph, "f").metadata


def test_a_router_imported_from_another_file_is_still_a_router(tmp_path):
    graph = parse(
        tmp_path,
        "views.py",
        'from .app import app, bp\n\n\n@app.get("/a")\ndef a():\n    pass\n\n\n'
        '@bp.route("/b")\ndef b():\n    pass\n',
    )
    assert pairs(func(graph, "a").metadata["routes"]) == [("GET", "/a")]
    assert pairs(func(graph, "b").metadata["routes"]) == [("GET", "/b")]


def test_a_fetch_with_a_method_that_is_not_a_literal_is_left_out(tmp_path):
    graph = parse(
        tmp_path,
        "client.js",
        "function a(m) { return fetch('/x', { method: m }); }\n"
        "function b() { return fetch('/y', { headers: {} }); }\n",
    )
    assert "http_calls" not in func(graph, "a").metadata
    assert pairs(func(graph, "b").metadata["http_calls"]) == [("GET", "/y")]


def test_the_module_node_keeps_more_facts_than_a_function(tmp_path):
    src = "const app = express();\n" + "".join(
        f'app.get("/r{i}", (req, res) => res.send("x"));\n' for i in range(120)
    )
    graph = parse(tmp_path, "big.js", src)
    assert len(module(graph).metadata["routes"]) == 120


# --- calls to a host outside the project (ADR-027, 2.4 audit) -------------------------------


def _call_entries(graph, name):
    return func(graph, name).metadata["http_calls"]


@pytest.mark.parametrize(
    "url,external",
    [
        ("https://api.stripe.com/v1/charges", True),
        ("http://partner.example.org/orders", True),
        ("//cdn.example.com/app.js", True),
        ("http://localhost:8000/orders", False),
        ("http://127.0.0.1:8000/orders", False),
        ("http://backend:8000/orders", False),
        ("/orders", False),
    ],
)
def test_a_python_call_to_a_host_with_a_dot_is_marked_external(tmp_path, url, external):
    graph = parse(
        tmp_path, "c.py", f'import requests\n\n\ndef c():\n    return requests.get("{url}")\n'
    )
    [entry] = _call_entries(graph, "c")
    assert entry.get("external", False) is external


def test_a_js_call_to_an_outside_host_is_marked_external(tmp_path):
    graph = parse(
        tmp_path, "c.ts", 'function c() { return fetch("https://api.stripe.com/v1/charges"); }\n'
    )
    [entry] = _call_entries(graph, "c")
    assert entry == {"method": "GET", "path": "/v1/charges", "external": True}


def test_a_base_url_variable_is_not_marked_external(tmp_path):
    graph = parse(tmp_path, "c.ts", "function c() { return fetch(`${API}/orders`); }\n")
    [entry] = _call_entries(graph, "c")
    assert "external" not in entry


def test_a_call_that_is_not_external_keeps_the_two_keys_only(tmp_path):
    graph = parse(tmp_path, "c.ts", 'function c() { return fetch("/orders"); }\n')
    assert _call_entries(graph, "c") == [{"method": "GET", "path": "/orders"}]


def test_the_extractor_version_is_three_or_more():
    from nexus_mcp.indexing.pipeline import EXTRACTOR_VERSION

    assert EXTRACTOR_VERSION >= 3
