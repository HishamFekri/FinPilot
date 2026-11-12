"""Security sweeps over every route, driven by the API's own description, on the test database.

New routes are covered automatically. Three properties:

- Anonymous: every route except sign-in and health answers 401.
- Isolation: a stranger using another person's IDs, in the path, body or query, gets nothing.
- Robustness: hostile input is refused or stored harmlessly, and is never a server error.
"""

import copy
import json
import re
import uuid
from datetime import timedelta
from typing import Any
from urllib.parse import quote

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.rate_limit import RateLimiter
from tests.test_fx import TODAY, providers  # noqa: F401 - the fixture
from tests.test_installments import _plan
from tests.test_links import _category_id, _setup
from tests.test_trips import API, Person, _expense, _ok, _trip, people  # noqa: F401 - fixtures

PUBLIC = {
    "/api/v1/healthz",
    "/api/v1/readyz",
    "/api/v1/auth/google/start",
    "/api/v1/auth/google/callback",
    "/api/v1/auth/logout",
    "/api/v1/openapi.json",
}
MARKER = "ZZ-PRIVATE-MARKER"
UNSAFE = ("post", "put", "patch", "delete")
Op = tuple[str, str, dict[str, Any]]


def operations(app: FastAPI) -> list[Op]:
    return [
        (method, path, operation)
        for path, item in app.openapi()["paths"].items()
        for method, operation in item.items()
    ]


def world(owner: Person, member: Person) -> dict[str, Any]:
    """One of everything, owned by `owner`, each name matching the API's parameter names."""
    account = _setup(owner)
    groceries = _category_id(owner, "groceries")
    spend = {
        "type": "expense", "account_id": account["id"], "category_id": groceries,
        "amount": {"amount": "12.00", "currency": "EUR"}, "description": MARKER,
    }  # fmt: skip
    txn = _ok(owner.post("/transactions", spend), 201)
    other = _ok(owner.post("/accounts", {
        "name": MARKER, "type": "wallet", "currency": "EUR",
        "opening_balance": {"amount": "50.00", "currency": "EUR"},
    }), 201)  # fmt: skip
    template = _ok(owner.post("/templates", {
        "name": MARKER, "kind": "expense", "currency": "EUR", "category_id": groceries,
    }), 201)  # fmt: skip
    goal = _ok(owner.post("/goals", {
        "name": MARKER, "target": {"amount": "100.00", "currency": "EUR"},
    }), 201)  # fmt: skip
    group = _ok(owner.post("/category-groups", {"name": MARKER}), 201)
    rule = _ok(owner.post("/recurring/rules", {
        "kind": "expense", "name": MARKER, "currency": "EUR", "frequency": "monthly",
        "amount": {"amount": "30.00", "currency": "EUR"}, "account_id": account["id"],
        "category_id": groceries, "window_start_day": 1, "window_end_day": 28,
        "start_date": TODAY.replace(day=1).isoformat(),
    }), 201)  # fmt: skip
    listed = _ok(owner.get("/recurring/occurrences"))
    occurrences = listed["items"] if isinstance(listed, dict) else listed
    due = (TODAY + timedelta(days=5)).isoformat()
    plan = _plan(owner, account, name=MARKER, item_price="400.00", down_payment="0.00",
                 installment_count=4, first_due_date=due)  # fmt: skip
    trip = _trip(owner, members=[member])
    expense = _expense(owner, trip, owner, "90.00", [owner, member], description=MARKER)
    invitation = _ok(owner.post(f"/trips/{trip}/invitations", {"label": MARKER}), 201)
    session = _ok(owner.get("/auth/sessions"))[0]
    version = expense["current_version"]
    dispute = _ok(member.post(f"/trips/{trip}/expenses/{expense['id']}/disputes", {
        "version_id": version["id"], "reason": MARKER,
    }), 201)  # fmt: skip
    settlement = _ok(member.post(f"/trips/{trip}/settlements", {
        "recipient_user_id": owner.id, "amount": {"amount": "10.00", "currency": "EUR"},
    }), 201)  # fmt: skip
    return {
        "account_id": account["id"],
        "from_account_id": account["id"],
        "to_account_id": other["id"],
        "transaction_id": txn["id"],
        "category_id": groceries,
        "template_id": template["id"],
        "goal_id": goal["id"],
        "group_id": group["id"],
        "rule_id": rule["id"],
        "occurrence_id": occurrences[0]["id"] if occurrences else str(uuid.uuid4()),
        "plan_id": plan["id"],
        "debt_id": plan.get("debt_id") or plan.get("debt", {}).get("id") or str(uuid.uuid4()),
        "trip_id": trip,
        "expense_id": expense["id"],
        "version_id": version.get("id") or str(uuid.uuid4()),
        "invitation_id": invitation["id"],
        "member_id": member.id,
        "session_id": session["id"],
        "item_id": str(uuid.uuid4()),
        "settlement_id": settlement["id"],
        "dispute_id": dispute["id"],
        "link_id": str(uuid.uuid4()),
        "action": "approve",
        "direction": "add",
    }


def schema_of(app: FastAPI, node: dict[str, Any]) -> dict[str, Any]:
    while "$ref" in node:
        node = app.openapi()["components"]["schemas"][node["$ref"].rsplit("/", 1)[1]]
    return node


def sample(app: FastAPI, node: dict[str, Any], name: str, ids: dict[str, Any]) -> Any:
    """A plausible value for a field, using the given IDs wherever the name matches."""
    node = schema_of(app, node)
    if "anyOf" in node:
        options = [o for o in node["anyOf"] if o.get("type") != "null"]
        return sample(app, options[0], name, ids)
    if "enum" in node:
        return node["enum"][0]
    if "const" in node:
        return node["const"]
    kind = node.get("type")
    if kind == "object" or "properties" in node:
        props = node.get("properties", {})
        wanted = set(node.get("required", [])) | {k for k in props if k in ids}
        return {k: sample(app, v, k, ids) for k, v in props.items() if k in wanted}
    if kind == "array":
        return [sample(app, node.get("items", {}), name, ids)]
    if kind == "integer":
        return max(node.get("minimum", 1), 1)
    if kind == "number":
        return 1
    if kind == "boolean":
        return True
    fmt = node.get("format")
    if fmt == "uuid" or name in ids:
        return ids.get(name, str(uuid.uuid4()))
    if fmt == "date":
        return TODAY.isoformat()
    if fmt == "date-time":
        return f"{TODAY.isoformat()}T12:00:00Z"
    if name in ("currency", "base_currency"):
        return "EUR"
    if name in ("amount", "price", "principal", "interest", "fee"):
        return "1.00"
    if name.endswith("month"):
        return TODAY.strftime("%Y-%m")
    return "x"


def body_for(app: FastAPI, operation: dict[str, Any], ids: dict[str, Any]) -> Any:
    content = operation.get("requestBody", {}).get("content", {}).get("application/json")
    return sample(app, content["schema"], "", ids) if content else None


def call(
    person: Person,
    app: FastAPI,
    method: str,
    path: str,
    ids: dict[str, Any],
    body: Any = None,
    params: dict[str, Any] | None = None,
) -> Any:
    # Limits are tested elsewhere: here they'd only hide what's behind them.
    app.state.rate_limiter = RateLimiter()
    url = re.sub(r"\{(\w+)\}", lambda m: quote(str(ids[m.group(1)])), path)
    headers = {**person.h(True), "If-Match": '"1"'} if method in UNSAFE else {}
    if body is None:
        return person.c.request(method.upper(), url, headers=headers, params=params)
    # Python's encoder escapes lone surrogates, so they arrive at the API as a real client's would.
    headers = {**headers, "Content-Type": "application/json"}
    return person.c.request(
        method.upper(), url, content=json.dumps(body), headers=headers, params=params
    )


def test_anonymous_callers_get_nothing_anywhere(auth_app: FastAPI) -> None:
    ids: dict[str, str] = {}

    class Ids(dict[str, str]):
        def __missing__(self, key: str) -> str:
            return str(uuid.uuid4())

    routes = operations(auth_app)
    assert len(routes) > 150, "the route list came back empty, so this would prove nothing"
    with TestClient(auth_app, raise_server_exceptions=False) as anonymous:
        for method, path, _ in routes:
            if path in PUBLIC:
                continue
            url = re.sub(r"\{(\w+)\}", lambda m: Ids(ids)[m.group(1)], path)
            response = anonymous.request(method.upper(), url, json={})
            assert response.status_code == 401, (method, path, response.status_code)
            assert response.json()["code"] == "not_authenticated"


def test_a_stranger_gets_nothing_with_someone_elses_ids(
    people: list[Person], auth_app: FastAPI
) -> None:
    alice, bob, carol, _ = people
    _setup(bob)
    ids = world(alice, carol)
    checked = 0
    for method, path, operation in operations(auth_app):
        names = re.findall(r"\{(\w+)\}", path)
        if not names or path in PUBLIC:
            continue
        body = body_for(auth_app, operation, ids) if method in UNSAFE else None
        response = call(bob, auth_app, method, path, ids, body)
        checked += 1
        where = (method, path, response.status_code, response.text[:120])
        assert response.status_code < 500, where
        assert not 200 <= response.status_code < 300, where
        assert MARKER not in response.text, where
    assert checked > 50


def test_ids_in_a_body_or_query_cant_reach_someone_elses_records(
    people: list[Person], auth_app: FastAPI
) -> None:
    alice, bob, carol, _ = people
    bob_account = _setup(bob)
    ids = world(alice, carol)
    leaked = []
    for method, path, operation in operations(auth_app):
        if re.search(r"\{\w+\}", path) or path in PUBLIC or "/auth/" in path:
            continue
        if method == "get":
            params = {
                p["name"]: ids[p["name"]]
                for p in operation.get("parameters", [])
                if p["in"] == "query" and p["name"] in ids
            }
            if not params:
                continue
            response = call(bob, auth_app, method, path, ids, params=params)
            assert response.status_code < 500, (path, response.text[:120])
            if MARKER in response.text:
                leaked.append(("query", path))
            continue
        body = body_for(auth_app, operation, ids)
        text = str(body)
        if not any(
            str(ids[k]) in text for k in ("account_id", "category_id", "goal_id", "rule_id")
        ):
            continue
        response = call(bob, auth_app, method, path, ids, body)
        assert response.status_code < 500, (method, path, response.text[:160])
        if 200 <= response.status_code < 300:
            leaked.append(("body", path))
    assert leaked == []
    assert bob_account  # Bob has records of his own, so empty answers mean isolation


HOSTILE_TEXT = ["\x00", "a\x00b", "\ud800", "x" * 300_000]
HOSTILE_DATES = ["0001-01-01", "9999-12-31"]
HOSTILE_QUERY = {
    "date": ["0001-01-01", "9999-12-31"],
    "month": ["0000-01", "9999-12"],
    "text": ["\x00", "'; DROP TABLE users; --", "%", "x" * 5000],
    "int": ["-1", "0", "99999999999999999999"],
}


def text_fields(app: FastAPI, node: dict[str, Any], prefix: tuple[str, ...] = ()) -> list[Any]:
    node = schema_of(app, node)
    if "anyOf" in node:
        node = schema_of(app, next(o for o in node["anyOf"] if o.get("type") != "null"))
    out: list[Any] = []
    for key, child in node.get("properties", {}).items():
        resolved = schema_of(app, child)
        if "anyOf" in resolved:
            resolved = schema_of(app, next(o for o in resolved["anyOf"] if o.get("type") != "null"))
        if resolved.get("type") == "object" or "properties" in resolved:
            out += text_fields(app, resolved, (*prefix, key))
        elif resolved.get("type") == "string" and resolved.get("format") in (None, "date"):
            out.append((*prefix, key))
    return out


def put(body: Any, path: tuple[str, ...], value: Any) -> Any:
    out = copy.deepcopy(body) if isinstance(body, dict) else {}
    node = out
    for key in path[:-1]:
        node = node.setdefault(key, {})
    node[path[-1]] = value
    return out


def test_hostile_text_in_any_field_is_never_a_server_error(
    people: list[Person], auth_app: FastAPI
) -> None:
    alice, _, carol, _ = people
    ids = world(alice, carol)
    failures = []
    for method, path, operation in operations(auth_app):
        content = operation.get("requestBody", {}).get("content", {}).get("application/json")
        if method not in UNSAFE or not content or path in PUBLIC:
            continue
        base = sample(auth_app, content["schema"], "", ids)
        for field in text_fields(auth_app, content["schema"]):
            is_date = field[-1].endswith(("date", "_on", "_from", "_to"))
            for value in HOSTILE_DATES if is_date else HOSTILE_TEXT:
                response = call(alice, auth_app, method, path, ids, put(base, field, value))
                if response.status_code >= 500:
                    failures.append((method, path, ".".join(field), repr(value[:6])))
    assert not failures, "\n".join(map(str, failures))


def test_hostile_query_values_are_never_a_server_error(
    people: list[Person], auth_app: FastAPI
) -> None:
    alice, _, carol, _ = people
    ids = world(alice, carol)
    failures = []
    for method, path, operation in operations(auth_app):
        if method != "get" or path in PUBLIC or path.endswith("export.pdf"):
            continue
        for parameter in operation.get("parameters", []):
            if parameter["in"] != "query":
                continue
            node = schema_of(auth_app, parameter["schema"])
            if "anyOf" in node:
                node = schema_of(
                    auth_app, next(o for o in node["anyOf"] if o.get("type") != "null")
                )
            name = parameter["name"]
            kind = (
                "date" if node.get("format") == "date"
                else "month" if "month" in name or name == "trend_end"
                else "int" if node.get("type") == "integer"
                else "text"
            )  # fmt: skip
            for value in HOSTILE_QUERY[kind]:
                response = call(alice, auth_app, method, path, ids, params={name: value})
                if response.status_code >= 500:
                    failures.append((path, name, value[:14]))
    assert not failures, "\n".join(map(str, failures))


HOSTILE_AMOUNTS = ["1e400", "NaN", "Infinity", "-1.00", "0.001", "99999999999999999999999.99", "٣"]


def test_hostile_amounts_are_refused_cleanly(people: list[Person]) -> None:
    alice, *_ = people
    account = _setup(alice)
    category = _category_id(alice, "groceries")
    for value in HOSTILE_AMOUNTS:
        body = {"type": "expense", "account_id": account["id"], "category_id": category,
                "amount": {"amount": value, "currency": "EUR"}}  # fmt: skip
        response = alice.post("/transactions", body)
        assert response.status_code < 500, (value, response.text[:160])
        assert not (value.startswith(("-", "1e", "N", "I")) and response.status_code < 300), value
