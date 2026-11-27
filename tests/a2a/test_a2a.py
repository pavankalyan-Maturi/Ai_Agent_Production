"""P3.4: signed A2A agent card, verified by the SDK client; JSON-RPC runs the agent (ADR 008)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from a2a.client import A2ACardResolver, ClientConfig, ClientFactory
from a2a.helpers import get_artifact_text, new_text_message
from a2a.types import GetTaskRequest, Role, SendMessageRequest, TaskState
from a2a.utils.signing import InvalidSignaturesError, NoSignatureError
from cryptography.hazmat.primitives.asymmetric import ec
from langgraph.checkpoint.memory import InMemorySaver

from agent.governance import Governance
from agent.graph import build_graph
from agent.model import ScriptedModel
from agent_a2a.card import JWKS_PATH, RPC_PATH, build_card, jwks, sign_card, verifier_from_jwks
from agent_a2a.server import build_a2a_app
from mcp_server.auth import JwtVerifier
from tests.agent.scripted import plan_policy
from tests.mcp.harness import FakeTool, token

BASE = "http://agent.test"
AUD = f"{BASE}{RPC_PATH}"
JKU = f"{BASE}{JWKS_PATH}"
KEY = "test-key-" + "x" * 32
ISS = "https://idp.test"


def a2a_token(tenant: str = "acme", **over: Any) -> str:
    return token(tenant, aud=AUD, scope=over.pop("scope", "pap:agent"), **over)


@pytest.fixture
def signer() -> ec.EllipticCurvePrivateKey:
    return ec.generate_private_key(ec.SECP256R1())


@pytest.fixture
def setup(signer: ec.EllipticCurvePrivateKey, tmp_path: Path) -> Any:
    search = FakeTool("search")
    model = ScriptedModel(plan_policy([("search", {"query": "reactor"}), "300 C [d1] ({last})"]))
    gov = Governance.from_yaml(audit_path=str(tmp_path / "audit.db"))
    graph = build_graph(model, [search.tool], governance=gov, checkpointer=InMemorySaver())
    card = sign_card(build_card(BASE, "0.3.0"), signer, "k1", JKU)
    app = build_a2a_app(graph, card, jwks(signer, "k1"), JwtVerifier(KEY, issuer=ISS, audience=AUD))
    yield app, search, gov
    gov.close()


def http(app: Any, headers: dict[str, str] | None = None) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=BASE, headers=headers or {}
    )


async def fetch_verified(app: Any) -> Any:
    async with http(app) as hc:
        keyset = (await hc.get(JWKS_PATH)).json()
        return await A2ACardResolver(hc, BASE).get_agent_card(
            signature_verifier=verifier_from_jwks(keyset, trusted_jku=JKU)
        )


# ---------- the card ----------


def test_card_is_signed_and_verified_by_the_sdk_client(setup: Any) -> None:
    app, _, _ = setup
    card = asyncio.run(fetch_verified(app))
    assert card.name == "pap-research-agent" and len(card.signatures) == 1
    [iface] = card.supported_interfaces
    assert (iface.url, iface.protocol_binding) == (AUD, "JSONRPC")
    assert [s.id for s in card.skills] == ["grounded-qa"]
    scheme = card.security_schemes["bearer"].http_auth_security_scheme
    assert scheme.scheme == "Bearer" and scheme.bearer_format == "JWT"
    assert list(card.security_requirements[0].schemes["bearer"].list) == ["pap:agent"]
    header = json.loads(__import__("base64").urlsafe_b64decode(card.signatures[0].protected + "=="))
    assert header == {"kid": "k1", "alg": "ES256", "jku": JKU, "typ": "JOSE"}


def test_tampered_card_is_rejected(setup: Any, signer: ec.EllipticCurvePrivateKey) -> None:
    app, _, _ = setup
    card = asyncio.run(fetch_verified(app))
    verify = verifier_from_jwks(jwks(signer, "k1"), trusted_jku=JKU)
    card.supported_interfaces[0].url = "http://evil.test/a2a/v1"  # redirect traffic
    with pytest.raises(InvalidSignaturesError):
        verify(card)
    del card.signatures[:]
    with pytest.raises(NoSignatureError):
        verify(card)


def test_card_signed_by_another_key_or_untrusted_jku_is_rejected(setup: Any) -> None:
    app, _, _ = setup
    other = ec.generate_private_key(ec.SECP256R1())
    forged = sign_card(build_card(BASE, "0.3.0"), other, "k1", JKU)  # same kid, wrong key

    async def keyset() -> dict[str, Any]:
        async with http(app) as hc:
            return dict((await hc.get(JWKS_PATH)).json())

    ks = asyncio.run(keyset())
    with pytest.raises(InvalidSignaturesError):
        verifier_from_jwks(ks, trusted_jku=JKU)(forged)
    evil = sign_card(build_card(BASE, "0.3.0"), other, "k1", "http://evil.test/jwks.json")
    with pytest.raises(InvalidSignaturesError):
        verifier_from_jwks(jwks(other, "k1"), trusted_jku=JKU)(evil)


def test_jwks_has_only_the_public_key(setup: Any) -> None:
    app, _, _ = setup

    async def go() -> dict[str, Any]:
        async with http(app) as hc:
            return dict((await hc.get(JWKS_PATH)).json())

    [k] = asyncio.run(go())["keys"]
    assert k["kty"] == "EC" and k["crv"] == "P-256" and "d" not in k


# ---------- JSON-RPC: the SDK client runs the agent ----------


async def ask(app: Any, tok: str, text: str) -> Any:
    async with http(app, {"Authorization": f"Bearer {tok}"}) as hc:
        card = await fetch_verified(app)
        client = ClientFactory(ClientConfig(httpx_client=hc, streaming=False)).create(card)
        msg = new_text_message(text, role=Role.ROLE_USER)
        events = [e async for e in client.send_message(SendMessageRequest(message=msg))]
        return events, client


def test_send_message_runs_the_agent_as_the_tokens_tenant(setup: Any) -> None:
    app, search, gov = setup
    events, _ = asyncio.run(ask(app, a2a_token("acme"), "how hot is the core?"))
    task = events[-1].task
    assert task.status.state == TaskState.TASK_STATE_COMPLETED
    answer = get_artifact_text(task.artifacts[0])
    assert answer.startswith("300 C [d1]") and "[acme-d1]" in answer
    assert search.calls == [("acme", {"query": "reactor"})]
    rows = gov.sentinel.audit.query(limit=5)  # type: ignore[union-attr]
    assert [(r.agent_id, r.tool, r.decision.value) for r in rows] == [
        ("researcher", "search", "allowed")
    ]


def test_tasks_are_isolated_per_tenant(setup: Any) -> None:
    app, _, _ = setup

    async def go() -> tuple[Any, Any]:
        events, _ = await ask(app, a2a_token("acme"), "q")
        task_id = events[-1].task.id
        own = other = None
        for tenant in ("acme", "globex"):
            async with http(app, {"Authorization": f"Bearer {a2a_token(tenant)}"}) as hc:
                card = await fetch_verified(app)
                c = ClientFactory(ClientConfig(httpx_client=hc, streaming=False)).create(card)
                try:
                    got = await c.get_task(GetTaskRequest(id=task_id))
                except Exception as exc:
                    got = exc
                if tenant == "acme":
                    own = got
                else:
                    other = got
        return own, other

    own, other = asyncio.run(go())
    assert own.status.state == TaskState.TASK_STATE_COMPLETED
    assert isinstance(other, Exception) and "not found" in str(other).lower()


@pytest.mark.parametrize(
    ("hdr", "status"),
    [
        ({}, 401),
        ({"Authorization": "Bearer nope"}, 401),
        ({"Authorization": f"Bearer {token('acme')}"}, 401),  # MCP audience, not A2A
        ({"Authorization": f"Bearer {a2a_token(scope='pap:search')}"}, 403),
    ],
    ids=["none", "garbage", "wrong-aud", "wrong-scope"],
)
def test_rpc_needs_an_a2a_token(setup: Any, hdr: dict[str, str], status: int) -> None:
    app, search, _ = setup
    body = {"jsonrpc": "2.0", "id": 1, "method": "SendMessage", "params": {}}

    async def go() -> httpx.Response:
        async with http(app, hdr) as hc:
            return await hc.post(RPC_PATH, json=body)

    r = asyncio.run(go())
    assert r.status_code == status and search.calls == []
    assert r.headers["www-authenticate"].startswith("Bearer")
