"""Test-only loopback OIDC provider for Phase 17 browser acceptance.

This module is never imported or mounted by the production application. Running
it requires explicit development-only environment flags and it binds only to
127.0.0.1.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import html
import os
import secrets
import time
from urllib.parse import parse_qs, urlencode

import jwt
import uvicorn
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse


TEST_SUBJECT = "phase17-local-admin"
TEST_CLIENT_ID = "finimatic-local-browser"


def _b64_uint(value: int) -> str:
    size = max(1, (value.bit_length() + 7) // 8)
    return base64.urlsafe_b64encode(value.to_bytes(size, "big")).decode("ascii").rstrip("=")


def _pkce_challenge(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).decode("ascii").rstrip("=")


def create_loopback_provider(*, port: int = 8018) -> FastAPI:
    if os.getenv("FINIMATIC_TEST_OIDC_ENABLED") != "1":
        raise RuntimeError("FINIMATIC_TEST_OIDC_ENABLED=1 is required")
    if os.getenv("FINIMATIC_ENVIRONMENT", "").strip().lower() not in {"development", "test"}:
        raise RuntimeError("loopback OIDC provider is restricted to development or test")
    app = FastAPI(title="Finimatic Phase 17 Test OIDC")
    issuer = f"http://127.0.0.1:{port}/oidc"
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_numbers = private_key.public_key().public_numbers()
    key_id = secrets.token_hex(8)
    pending_codes: dict[str, dict[str, str]] = {}

    app.state.issuer = issuer
    app.state.private_key = private_key
    app.state.pending_codes = pending_codes

    @app.get("/oidc/jwks.json")
    def jwks():
        return {
            "keys": [
                {
                    "kty": "RSA",
                    "use": "sig",
                    "alg": "RS256",
                    "kid": key_id,
                    "n": _b64_uint(public_numbers.n),
                    "e": _b64_uint(public_numbers.e),
                }
            ]
        }

    @app.get("/oidc/authorize", response_class=HTMLResponse)
    def authorize(
        response_type: str,
        client_id: str,
        redirect_uri: str,
        scope: str,
        state: str,
        nonce: str,
        code_challenge: str,
        code_challenge_method: str,
    ):
        if (
            response_type != "code"
            or client_id != TEST_CLIENT_ID
            or code_challenge_method != "S256"
            or "openid" not in scope.split()
            or not redirect_uri.startswith("http://127.0.0.1:5173/api/auth/callback")
        ):
            raise HTTPException(status_code=400, detail="invalid_authorization_request")
        fields = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "state": state,
            "nonce": nonce,
            "code_challenge": code_challenge,
        }
        hidden = "".join(
            f'<input type="hidden" name="{html.escape(key)}" value="{html.escape(value)}">'
            for key, value in fields.items()
        )
        return HTMLResponse(
            "<!doctype html><html><head><title>Finimatic Local Identity</title></head>"
            "<body><main><h1>Finimatic Local Test Identity</h1>"
            f"<p>Identity: {TEST_SUBJECT}</p><p>Role: admin</p>"
            "<p>This provider is loopback-only and development-only.</p>"
            f'<form method="post" action="/oidc/authorize">{hidden}'
            '<button type="submit">Continue as Phase 17 Admin</button></form>'
            "</main></body></html>"
        )

    @app.post("/oidc/authorize")
    async def approve(request: Request):
        form = parse_qs((await request.body()).decode("utf-8"), keep_blank_values=True)
        values = {key: rows[0] for key, rows in form.items() if rows}
        required = {"client_id", "redirect_uri", "state", "nonce", "code_challenge"}
        if not required.issubset(values) or values["client_id"] != TEST_CLIENT_ID:
            raise HTTPException(status_code=400, detail="invalid_authorization_request")
        code = secrets.token_urlsafe(32)
        pending_codes[code] = {
            "client_id": values["client_id"],
            "redirect_uri": values["redirect_uri"],
            "nonce": values["nonce"],
            "code_challenge": values["code_challenge"],
        }
        return RedirectResponse(
            f'{values["redirect_uri"]}?{urlencode({"code": code, "state": values["state"]})}',
            status_code=302,
        )

    @app.post("/oidc/token")
    async def token(request: Request):
        form = parse_qs((await request.body()).decode("utf-8"), keep_blank_values=True)
        values = {key: rows[0] for key, rows in form.items() if rows}
        code = values.get("code", "")
        pending = pending_codes.pop(code, None)
        if (
            pending is None
            or values.get("grant_type") != "authorization_code"
            or values.get("client_id") != pending["client_id"]
            or values.get("redirect_uri") != pending["redirect_uri"]
            or _pkce_challenge(values.get("code_verifier", "")) != pending["code_challenge"]
        ):
            return JSONResponse({"error": "invalid_grant"}, status_code=400)
        now = int(time.time())
        id_token = jwt.encode(
            {
                "iss": issuer,
                "aud": TEST_CLIENT_ID,
                "sub": TEST_SUBJECT,
                "iat": now,
                "exp": now + 600,
                "nonce": pending["nonce"],
                "email": "phase17-admin@finimatic.test",
            },
            private_key,
            algorithm="RS256",
            headers={"kid": key_id},
        )
        return {
            "token_type": "Bearer",
            "expires_in": 600,
            "id_token": id_token,
            "access_token": secrets.token_urlsafe(32),
        }

    return app


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8018)
    args = parser.parse_args()
    if os.getenv("FINIMATIC_TEST_OIDC_ENABLED") != "1":
        raise SystemExit("FINIMATIC_TEST_OIDC_ENABLED=1 is required")
    if os.getenv("FINIMATIC_ENVIRONMENT", "").strip().lower() != "development":
        raise SystemExit("loopback OIDC provider is restricted to development")
    uvicorn.run(create_loopback_provider(port=args.port), host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
