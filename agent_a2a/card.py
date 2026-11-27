"""Agent card, signed with JWS (ES256) over the RFC 8785-canonicalized card.

The private key comes from env (PAP_A2A_SIGNING_KEY, PEM). The public half is served as a
JWKS at /.well-known/jwks.json and named by `kid` + `jku` in the protected header, so a
client can verify the card it fetched before trusting anything in it.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import jwt
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    AgentProvider,
    AgentSkill,
    HTTPAuthSecurityScheme,
    SecurityRequirement,
    SecurityScheme,
    StringList,
)
from a2a.utils.signing import ProtectedHeader, create_agent_card_signer, create_signature_verifier
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import load_pem_private_key

ALG = "ES256"
JWKS_PATH = "/.well-known/jwks.json"
RPC_PATH = "/a2a/v1"
SCOPE = "pap:agent"


def build_card(base_url: str, version: str) -> AgentCard:
    base = base_url.rstrip("/")
    bearer = "bearer"
    return AgentCard(
        name="pap-research-agent",
        description=(
            "Answers questions over this tenant's documents with cited sources. Read-only: "
            "it searches and reads documents, it does not change anything."
        ),
        version=version,
        provider=AgentProvider(organization="prod-agent-platform", url=base),
        supported_interfaces=[
            AgentInterface(
                url=f"{base}{RPC_PATH}", protocol_binding="JSONRPC", protocol_version="1.0"
            )
        ],
        capabilities=AgentCapabilities(streaming=False, push_notifications=False),
        security_schemes={
            bearer: SecurityScheme(
                http_auth_security_scheme=HTTPAuthSecurityScheme(
                    scheme="Bearer",
                    bearer_format="JWT",
                    description=f"OAuth access token: aud={base}{RPC_PATH}, scope {SCOPE}, "
                    "tenant_id claim. The tenant comes only from the token.",
                )
            )
        },
        security_requirements=[SecurityRequirement(schemes={bearer: StringList(list=[SCOPE])})],
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
        skills=[
            AgentSkill(
                id="grounded-qa",
                name="Grounded question answering",
                description="Searches the caller's tenant documents and answers with [doc_id] "
                "citations. Says so when the documents do not contain the answer.",
                tags=["rag", "search", "citations"],
                examples=["What temperature does the reactor core run at?"],
                input_modes=["text/plain"],
                output_modes=["text/plain"],
            )
        ],
    )


def signing_key(pem: str) -> ec.EllipticCurvePrivateKey:
    key = load_pem_private_key(pem.encode(), password=None)
    if not isinstance(key, ec.EllipticCurvePrivateKey) or key.curve.name != "secp256r1":
        raise ValueError("PAP_A2A_SIGNING_KEY must be an EC P-256 private key (ES256)")
    return key


def sign_card(card: AgentCard, key: ec.EllipticCurvePrivateKey, kid: str, jku: str) -> AgentCard:
    header: ProtectedHeader = {"kid": kid, "alg": ALG, "jku": jku, "typ": "JOSE"}
    return create_agent_card_signer(key, header)(card)  # type: ignore[arg-type]


def jwks(key: ec.EllipticCurvePrivateKey, kid: str) -> dict[str, Any]:
    pub = json.loads(jwt.algorithms.ECAlgorithm.to_jwk(key.public_key()))
    pub.update({"kid": kid, "alg": ALG, "use": "sig"})
    return {"keys": [pub]}


def verifier_from_jwks(keyset: dict[str, Any], *, trusted_jku: str) -> Callable[[AgentCard], None]:
    """Client side: accept only keys from the JWKS we fetched from a jku we trust."""
    keys = {k["kid"]: jwt.PyJWK(k) for k in keyset.get("keys", [])}

    def provider(kid: str | None, jku: str | None) -> jwt.PyJWK:
        if jku != trusted_jku or kid not in keys:
            raise jwt.InvalidKeyError(f"untrusted key kid={kid!r} jku={jku!r}")
        return keys[kid]

    return create_signature_verifier(provider, [ALG])
