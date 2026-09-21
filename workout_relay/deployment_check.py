"""Read-only HTTP deployment checks. Never imports the app or reads secrets."""

import argparse
from urllib.parse import urlsplit

import httpx


class CheckFailed(Exception):
    pass


def origin(value):
    parsed = urlsplit(value)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username
            or parsed.password or parsed.query or parsed.fragment
            or parsed.path not in ("", "/")):
        raise ValueError("Provide an HTTPS origin without credentials, a path, query or fragment")
    return value.rstrip("/")


def check(base_url, client):
    base_url = origin(base_url)
    passed = []

    def get(path, label, json=True):
        try:
            response = client.get(base_url + path)
            if response.status_code != 200:
                raise CheckFailed(f"{label}: HTTP {response.status_code}")
            return response.json() if json else response.text
        except (httpx.HTTPError, ValueError):
            # Do not echo upstream bodies or exception URLs into CI logs.
            raise CheckFailed(f"{label}: unavailable or invalid response") from None

    def require(condition, label):
        if not condition:
            raise CheckFailed(label)
        passed.append(label)

    ready = get("/readyz", "readiness")
    require(isinstance(ready, dict) and ready.get("status") == "ready", "database readiness")
    health = get("/healthz", "health")
    require(isinstance(health, dict) and health.get("status") == "ok"
            and health.get("garmin_mode") == "live", "live-mode configuration")
    require("Workout Relay" in get("/", "homepage", json=False), "homepage")
    auth = get("/.well-known/oauth-authorization-server", "OAuth discovery")
    require(isinstance(auth, dict) and auth.get("issuer", "").rstrip("/") == base_url,
            "OAuth issuer matches target")
    for key, path in (("authorization_endpoint", "/authorize"), ("token_endpoint", "/token"),
                      ("registration_endpoint", "/register")):
        require(auth.get(key) == base_url + path, key)
    require({"plans:read", "plans:write", "activities:read"}.issubset(auth.get("scopes_supported", [])),
            "OAuth scopes")
    resource = get("/.well-known/oauth-protected-resource/mcp/", "MCP discovery")
    require(isinstance(resource, dict) and resource.get("resource", "").rstrip("/") == base_url + "/mcp",
            "MCP resource matches target")
    require(any(isinstance(issuer, str) and issuer.rstrip("/") == base_url
                for issuer in resource.get("authorization_servers", [])), "MCP authorization server")
    return passed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("base_url", type=origin)
    args = parser.parse_args()
    try:
        with httpx.Client(timeout=20, follow_redirects=False, trust_env=False) as client:
            for item in check(args.base_url, client):
                print(f"PASS: {item}")
    except CheckFailed as exc:
        parser.exit(1, f"FAIL: {exc}\n")
    print("No credentials sent or Garmin operations performed. User-flow verification still required.")


if __name__ == "__main__":
    main()
