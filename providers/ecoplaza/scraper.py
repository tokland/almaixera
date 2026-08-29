"""Extract all products from the ecoplaza inaCatalog B2B store into a JSON file.

The store (``ecoplaza.b2binacatalog.com``) runs inaCatalog, an ASP.NET Core B2B
platform. Extraction works in three steps:

1. Authenticate against ``/Usuario/Login`` (credentials are ``_enc_`` + base64).
2. Fetch the full list of product codes from ``/Articulo/DameArticulosFiltroGlobal``.
3. Fetch full product details in batches from ``/Articulo/ObtenerArticulos``.

Products are written **raw** — exactly the key/value objects the backend returns,
with no transformation. The module is fully type annotated and depends only on
the standard library, so it runs anywhere with no ``pip install`` step.
"""

from __future__ import annotations

import argparse
import base64
import http.cookiejar
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Final

BASE_URL: Final = "https://ecoplaza.b2binacatalog.com"
DEFAULT_USER: Final = "Web-1598"
DEFAULT_PASSWORD: Final = "N1LL1BMH"
DEFAULT_BATCH_SIZE: Final = 200
DEFAULT_OUTPUT: Final = "products.json"

# Network resilience: retry transient failures with exponential backoff.
REQUEST_TIMEOUT: Final = 120
RETRY_ATTEMPTS: Final = 4
RETRY_BACKOFF_BASE: Final = 2.0  # seconds: waits 2, 4, 8 between attempts
RETRYABLE_STATUS: Final = frozenset({429, 500, 502, 503, 504})

# A raw product is just the JSON object inaCatalog returns, keys unchanged.
RawProduct = dict[str, Any]


def _sleep_backoff(attempt: int, label: str, cause: str) -> None:
    """Log a transient failure and sleep before the next retry."""
    wait = RETRY_BACKOFF_BASE * (2 ** (attempt - 1))
    print(
        f"  {label}: {cause}, retrying in {wait:.0f}s "
        f"(attempt {attempt + 1}/{RETRY_ATTEMPTS})",
        file=sys.stderr,
    )
    time.sleep(wait)


# --------------------------------------------------------------------------- #
# HTTP client
# --------------------------------------------------------------------------- #
class InaCatalogError(RuntimeError):
    """Raised when the inaCatalog backend returns an unexpected response."""


def _encode_credential(value: str) -> str:
    """Reproduce the site's ``encodeParaUrl``: ``_enc_`` + base64 (``/`` → ``_``)."""
    encoded = base64.b64encode(value.encode("utf-8")).decode("ascii")
    return "_enc_" + encoded.replace("/", "_")


class EcoplazaClient:
    """A thin, typed HTTP client for the ecoplaza inaCatalog backend."""

    def __init__(self, base_url: str = BASE_URL) -> None:
        self._base_url = base_url.rstrip("/")
        self._cookies = http.cookiejar.CookieJar()
        self._opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self._cookies)
        )

    # -- low level -------------------------------------------------------- #
    def _request(
        self,
        method: str,
        path: str,
        *,
        data: bytes | None = None,
        content_type: str | None = None,
    ) -> bytes:
        url = f"{self._base_url}{path}"
        headers: dict[str, str] = {
            "User-Agent": "Mozilla/5.0 (ecoplaza-scraper)",
            "X-Requested-With": "XMLHttpRequest",
            "Origin": self._base_url,
            "Referer": f"{self._base_url}/Portada",
        }
        if content_type is not None:
            headers["Content-Type"] = content_type
        request = urllib.request.Request(url, data=data, method=method, headers=headers)

        # Transient network / server errors: retry with exponential backoff.
        # Every request here is idempotent (a GET, or a read-only POST), so a
        # retry can never duplicate an effect.
        for attempt in range(1, RETRY_ATTEMPTS + 1):
            try:
                with self._opener.open(request, timeout=REQUEST_TIMEOUT) as response:
                    body: bytes = response.read()
                    return body
            except urllib.error.HTTPError as exc:  # pragma: no cover - network path
                if exc.code in RETRYABLE_STATUS and attempt < RETRY_ATTEMPTS:
                    _sleep_backoff(attempt, f"{method} {path}", f"HTTP {exc.code}")
                    continue
                error_body = exc.read().decode("utf-8", "replace")[:200]
                raise InaCatalogError(
                    f"{method} {path} -> HTTP {exc.code}: {error_body!r}"
                ) from exc
            except OSError as exc:  # URLError/timeout/conn reset all subclass OSError
                if attempt < RETRY_ATTEMPTS:
                    _sleep_backoff(attempt, f"{method} {path}", type(exc).__name__)
                    continue
                raise InaCatalogError(f"{method} {path} -> {exc}") from exc
        raise InaCatalogError(f"{method} {path}: exhausted retries")  # unreachable

    def _post_form(self, path: str, fields: dict[str, str]) -> bytes:
        body = urllib.parse.urlencode(fields).encode("utf-8")
        return self._request(
            "POST",
            path,
            data=body,
            content_type="application/x-www-form-urlencoded; charset=UTF-8",
        )

    def _post_json(self, path: str, payload: object) -> Any:
        body = json.dumps(payload).encode("utf-8")
        raw = self._request(
            "POST",
            path,
            data=body,
            content_type="application/json; charset=UTF-8",
        )
        return json.loads(raw) if raw else None

    # -- high level ------------------------------------------------------- #
    def login(self, user: str, password: str) -> None:
        """Authenticate and establish an inaCatalog session."""
        # A GET first, so the server hands us a session cookie to authenticate.
        self._request("GET", "/")
        fields = {
            "datLoginEncoded": _encode_credential(user),
            "datPasswordEncoded": _encode_credential(password),
            "codIdioma": "es",
        }
        body = self._post_form("/Usuario/Login", fields).decode("utf-8", "replace")
        # An empty body signals success; any text is an error message shown to the user.
        if body.strip():
            raise InaCatalogError(f"Login failed: {body.strip()[:200]}")
        # Confirm the session is authenticated by loading the portal.
        portal = self._request("GET", "/Portada").decode("utf-8", "replace")
        if 'id="login-container"' in portal:
            raise InaCatalogError("Login appeared to succeed but session is not authenticated")

    def list_product_codes(self) -> list[str]:
        """Return every product code from the global filter endpoint.

        inaCatalog's "Dame*/Render" endpoints reject requests without a form
        content-type (HTTP 400), even though this one takes no parameters.
        """
        raw = self._post_form("/Articulo/DameArticulosFiltroGlobal", {})
        data = json.loads(raw) if raw else None
        if not isinstance(data, dict) or "articulos" not in data:
            raise InaCatalogError("Unexpected DameArticulosFiltroGlobal response shape")
        articulos = data["articulos"]
        if not isinstance(articulos, list):
            raise InaCatalogError("DameArticulosFiltroGlobal 'articulos' is not a list")
        codes: list[str] = []
        for item in articulos:
            if isinstance(item, dict):
                code = item.get("codArticulo")
                if isinstance(code, str) and code:
                    codes.append(code)
        return codes

    def fetch_details(self, codes: list[str]) -> dict[str, RawProduct]:
        """Fetch raw product objects for a batch of codes (code → raw object)."""
        result = self._post_json("/Articulo/ObtenerArticulos/?t", codes)
        if not isinstance(result, dict):
            raise InaCatalogError("Unexpected ObtenerArticulos response shape")
        return {str(k): v for k, v in result.items() if isinstance(v, dict)}


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def _chunks(items: list[str], size: int) -> list[list[str]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def scrape(
    user: str,
    password: str,
    *,
    limit: int | None = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> list[RawProduct]:
    """Log in and return the store's products as raw, untransformed objects."""
    client = EcoplazaClient()
    print("Logging in...", file=sys.stderr)
    client.login(user, password)

    print("Fetching product code list...", file=sys.stderr)
    codes = client.list_product_codes()
    print(f"  {len(codes)} product codes found", file=sys.stderr)
    if limit is not None:
        codes = codes[:limit]
        print(f"  limited to {len(codes)} for this run", file=sys.stderr)

    products: list[RawProduct] = []
    batches = _chunks(codes, batch_size)
    for index, batch in enumerate(batches, start=1):
        details = client.fetch_details(batch)
        # Preserve the input order and skip codes the backend didn't return.
        for code in batch:
            detail = details.get(code)
            if detail is not None:
                products.append(detail)
        print(
            f"  batch {index}/{len(batches)}: {len(products)} products so far",
            file=sys.stderr,
        )
    return products


def products_to_json(products: list[RawProduct]) -> str:
    """Serialise the raw products to a pretty JSON string."""
    payload = {
        "source": BASE_URL,
        "count": len(products),
        "products": products,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user", default=DEFAULT_USER, help="login user")
    parser.add_argument("--password", default=DEFAULT_PASSWORD, help="login password")
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help="output JSON path")
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="only extract the first N products (for testing)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help="how many products to request per detail call",
    )
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = _parse_args(argv)
    products = scrape(
        args.user,
        args.password,
        limit=args.limit,
        batch_size=args.batch_size,
    )
    with open(args.output, "w", encoding="utf-8") as handle:
        handle.write(products_to_json(products))
    print(f"Wrote {len(products)} products to {args.output}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
