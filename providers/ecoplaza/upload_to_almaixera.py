"""Sync extracted ecoplaza products into the almaixera app for provider "Ecoplaza".

Reads the raw JSON produced by ``scraper.py`` and reconciles it against the
almaixera product list exposed by the Django REST Framework API
(``/comandes/api/productes/``):

* products in the JSON are **created or updated** under the Ecoplaza provider,
  via a single bulk POST that the API treats as an upsert;
* products whose data is unchanged are **left untouched** (no write);
* Ecoplaza products that are no longer in the JSON are **disabled**
  (``actiu = False``), never deleted.

Price is the ecoplaza **PVP** (``pvp``). Products with no positive PVP are
still imported, but **disabled** and priced at 0 (see :func:`map_product`).

Matching between an ecoplaza product and an almaixera product is done by the
ecoplaza product code (SKU), which is stored in the almaixera product's
``notes`` field behind a marker (see :data:`SKU_MARKER`). The API keys its
upsert on the same marker, so re-runs update in place instead of duplicating.

Use ``--dry-run`` to preview the plan without writing anything.

Fully type annotated, standard library only.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Final

ALMAIXERA_BASE: Final = "http://localhost:5000"
API_PATH: Final = "/comandes/api"
DEFAULT_PROVIDER_ID: Final = 65          # Proveidor "Ecoplaza"
DEFAULT_PROVIDER_NAME: Final = "Ecoplaza"
DEFAULT_USER: Final = "admin"

# Network resilience: retry transient failures with exponential backoff.
REQUEST_TIMEOUT: Final = 120
RETRY_ATTEMPTS: Final = 4
RETRY_BACKOFF_BASE: Final = 2.0  # seconds: waits 2, 4, 8 between attempts
RETRYABLE_STATUS: Final = frozenset({429, 500, 502, 503, 504})

# almaixera Producte.preu is DecimalField(max_digits=5, decimal_places=2).
MAX_PRICE: Final = Decimal("999.99")
PRICE_QUANT: Final = Decimal("0.01")
NAME_MAX_LEN: Final = 200

# Marker written into Producte.notes so a re-run can match the same product.
SKU_MARKER: Final = "ecoplaza-sku:"
_SKU_RE: Final = re.compile(re.escape(SKU_MARKER) + r"\s*(\S+)")


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
# Domain: the target almaixera product shape
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ProductePayload:
    """The almaixera ``Producte`` fields we manage for one ecoplaza product."""

    sku: str                 # ecoplaza codarticulo (natural key, kept in notes)
    nom: str                 # product name
    preu: str                # decimal string, the ecoplaza PVP
    notes: str               # includes the SKU marker
    proveidor: int           # Proveidor id
    actiu: bool = True
    stock: bool = False
    granel: bool = False     # ecoplaza items are unit-priced (€/unitat-manat)

    def api_body(self) -> dict[str, Any]:
        """The JSON body for a create/update request."""
        return {
            "nom": self.nom,
            "preu": self.preu,
            "notes": self.notes,
            "proveidor": self.proveidor,
            "actiu": self.actiu,
            "stock": self.stock,
            "granel": self.granel,
        }


@dataclass(frozen=True)
class ExistingProduct:
    """An almaixera product already stored under the Ecoplaza provider.

    Carries the managed fields so a re-sync can tell an unchanged product from
    one that actually needs a PATCH.
    """

    id: int
    nom: str
    preu: str
    notes: str
    actiu: bool
    stock: bool
    granel: bool
    sku: str | None          # parsed from notes, if present

    def matches(self, payload: ProductePayload) -> bool:
        """True when a write would be a no-op (nothing we manage differs)."""
        return not self.changes(payload)

    def changes(self, payload: ProductePayload) -> list[str]:
        """List the managed fields that differ, as ``old -> new`` strings."""
        diffs: list[str] = []
        if self.nom != payload.nom:
            diffs.append(f'nom "{self.nom}" -> "{payload.nom}"')
        if _to_decimal(self.preu) != _to_decimal(payload.preu):
            diffs.append(f"preu {self.preu} -> {payload.preu}")
        if self.actiu != payload.actiu:
            diffs.append(f"actiu {self.actiu} -> {payload.actiu}")
        if self.stock != payload.stock:
            diffs.append(f"stock {self.stock} -> {payload.stock}")
        if self.granel != payload.granel:
            diffs.append(f"granel {self.granel} -> {payload.granel}")
        if (self.notes or "").strip() != payload.notes.strip():
            diffs.append("notes changed")
        return diffs


@dataclass(frozen=True)
class SyncPlan:
    """The reconciled set of actions for one sync run."""

    to_create: list[ProductePayload]                     # POST, no existing match
    to_update: list[tuple[ExistingProduct, ProductePayload]]  # POST, matched+changed
    to_disable: list[ExistingProduct]                    # DELETE (soft-disable)
    unchanged: list[ExistingProduct]               # matched, nothing to do
    skipped: list[tuple[str, str, str]]            # (sku, nom, reason)


# --------------------------------------------------------------------------- #
# Mapping ecoplaza raw -> almaixera payload
# --------------------------------------------------------------------------- #
def _to_decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value).replace("€", "").replace(",", ".").strip())
    except (InvalidOperation, ValueError):
        return None


def compute_price(pvp: Any) -> Decimal | None:
    """Return the product price (the ecoplaza PVP), rounded to 2 decimals."""
    base = _to_decimal(pvp)
    if base is None:
        return None
    return base.quantize(PRICE_QUANT, rounding=ROUND_HALF_UP)


def build_notes(raw: dict[str, Any]) -> str:
    """Compose the notes field, embedding the SKU marker plus useful references."""
    sku = str(raw.get("codarticulo", "")).strip()
    lines = [f"{SKU_MARKER} {sku}"]
    ean = str(raw.get("codean") or "").strip()
    if ean:
        lines.append(f"EAN: {ean}")
    return "\n".join(lines)


def map_product(raw: dict[str, Any], provider_id: int) -> ProductePayload | None:
    """Map one raw ecoplaza product to a :class:`ProductePayload`.

    Products with no positive PVP are still imported, but **disabled**
    (``actiu = False``) and priced at 0 — they have no retail price in
    ecoplaza (bulk/kg formats, testers, sets). Returns None (a real skip) only
    when the product lacks a usable code or name.
    """
    sku = str(raw.get("codarticulo", "")).strip()
    nom = str(raw.get("desarticulo", "")).strip()[:NAME_MAX_LEN]
    if not sku or not nom:
        return None
    price = compute_price(raw.get("pvp"))
    if price is None or price <= 0:
        return ProductePayload(
            sku=sku,
            nom=nom,
            preu="0.00",
            notes=build_notes(raw),
            proveidor=provider_id,
            actiu=False,
        )
    if price > MAX_PRICE:
        price = MAX_PRICE
    return ProductePayload(
        sku=sku,
        nom=nom,
        preu=f"{price:.2f}",
        notes=build_notes(raw),
        proveidor=provider_id,
        actiu=True,
    )


def skip_reason(raw: dict[str, Any]) -> str:
    """Explain why :func:`map_product` rejected a product (for the skip log).

    Only missing code/name are true skips now; a missing price no longer skips
    (the product is imported disabled instead).
    """
    if not str(raw.get("codarticulo", "")).strip():
        return "missing code"
    return "missing name"


def parse_sku(notes: str | None) -> str | None:
    if not notes:
        return None
    match = _SKU_RE.search(notes)
    return match.group(1) if match else None


# --------------------------------------------------------------------------- #
# almaixera REST client
# --------------------------------------------------------------------------- #
class AlmaixeraError(RuntimeError):
    """Raised when the almaixera API returns an unexpected response."""


class AlmaixeraClient:
    """Minimal typed client for the almaixera DRF API (HTTP Basic auth)."""

    def __init__(self, base_url: str, user: str, password: str) -> None:
        self._base = base_url.rstrip("/")
        token = base64.b64encode(f"{user}:{password}".encode("utf-8")).decode("ascii")
        self._auth = f"Basic {token}"

    def _request(
        self,
        method: str,
        url: str,
        body: dict[str, Any] | list[Any] | None = None,
    ) -> Any:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        headers = {
            "Authorization": self._auth,
            "Accept": "application/json",
        }
        if data is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=data, method=method, headers=headers)

        # Retry transient network / server errors with exponential backoff.
        # Every call is idempotent (GET, upsert POST keyed on SKU, or DELETE
        # soft-disable), so a retry cannot duplicate an effect.
        for attempt in range(1, RETRY_ATTEMPTS + 1):
            try:
                with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
                    raw = response.read()
                    return json.loads(raw) if raw else None
            except urllib.error.HTTPError as exc:  # pragma: no cover - network path
                if exc.code in RETRYABLE_STATUS and attempt < RETRY_ATTEMPTS:
                    _sleep_backoff(attempt, f"{method} {url}", f"HTTP {exc.code}")
                    continue
                detail = exc.read().decode("utf-8", "replace")[:300]
                raise AlmaixeraError(
                    f"{method} {url} -> HTTP {exc.code}: {detail}"
                ) from exc
            except OSError as exc:  # URLError/timeout/conn reset all subclass OSError
                if attempt < RETRY_ATTEMPTS:
                    _sleep_backoff(attempt, f"{method} {url}", type(exc).__name__)
                    continue
                raise AlmaixeraError(f"{method} {url} -> {exc}") from exc
        raise AlmaixeraError(f"{method} {url}: exhausted retries")  # unreachable

    # -- reads (available today) ----------------------------------------- #
    def list_provider_products(self, provider_id: int) -> list[ExistingProduct]:
        """Return every product for a provider, active or not, across all pages.

        We drive pagination with our own page counter rather than following the
        server's ``next`` URL: in production those links carry the app's
        internal hostname, which is not resolvable from outside.
        """
        out: list[ExistingProduct] = []
        page_num = 1
        while True:
            url = (
                f"{self._base}{API_PATH}/productes/"
                f"?proveidor={provider_id}&page={page_num}"
            )
            page = self._request("GET", url)
            if not isinstance(page, dict) or "results" not in page:
                raise AlmaixeraError("Unexpected product list response shape")
            for item in page["results"]:
                notes = str(item.get("notes") or "")
                out.append(
                    ExistingProduct(
                        id=int(item["id"]),
                        nom=str(item.get("nom", "")),
                        preu=str(item.get("preu", "")),
                        notes=notes,
                        actiu=bool(item.get("actiu")),
                        stock=bool(item.get("stock")),
                        granel=bool(item.get("granel")),
                        sku=parse_sku(notes),
                    )
                )
            if not page.get("next"):
                break
            page_num += 1
        return out

    # -- writes ---------------------------------------------------------- #
    def upsert_products(self, payloads: list[ProductePayload]) -> None:
        """Create-or-update products in one bulk POST.

        The API treats POST as an upsert keyed on the ``ecoplaza-sku:`` marker
        in ``notes``: a payload matching an existing product updates it in
        place, otherwise it is created. A list body upserts the batch atomically.
        """
        if not payloads:
            return
        url = f"{self._base}{API_PATH}/productes/"
        self._request("POST", url, [p.api_body() for p in payloads])

    def disable_product(self, product_id: int) -> None:
        """Disable via DELETE, which the API implements as a soft-disable."""
        url = f"{self._base}{API_PATH}/productes/{product_id}/"
        self._request("DELETE", url)


# --------------------------------------------------------------------------- #
# Reconciliation
# --------------------------------------------------------------------------- #
def load_raw_products(path: str) -> list[dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    products = data.get("products") if isinstance(data, dict) else data
    if not isinstance(products, list):
        raise AlmaixeraError(f"{path}: expected a list of products")
    return [p for p in products if isinstance(p, dict)]


def build_plan(
    raw_products: list[dict[str, Any]],
    existing: list[ExistingProduct],
    provider_id: int,
    limit: int | None,
) -> SyncPlan:
    """Reconcile the JSON against what already exists under the provider.

    ``limit`` caps how many products we create/update (for exploration), but the
    disable set is computed against the *full* JSON so a limited run never
    disables products that are legitimately present in the file.
    """
    all_skus: set[str] = {
        str(p.get("codarticulo", "")).strip()
        for p in raw_products
        if str(p.get("codarticulo", "")).strip()
    }
    existing_by_sku: dict[str, ExistingProduct] = {
        e.sku: e for e in existing if e.sku
    }

    selected = raw_products if limit is None else raw_products[:limit]

    to_create: list[ProductePayload] = []
    to_update: list[tuple[ExistingProduct, ProductePayload]] = []
    unchanged: list[ExistingProduct] = []
    skipped: list[tuple[str, str, str]] = []

    for raw in selected:
        sku = str(raw.get("codarticulo", "")).strip()
        nom = str(raw.get("desarticulo", "")).strip()
        payload = map_product(raw, provider_id)
        if payload is None:
            skipped.append((sku or "<no-sku>", nom, skip_reason(raw)))
            continue
        match = existing_by_sku.get(payload.sku)
        if match is None:
            to_create.append(payload)
        elif match.matches(payload):
            unchanged.append(match)
        else:
            to_update.append((match, payload))

    to_disable = [
        e
        for e in existing
        if e.actiu and (e.sku is None or e.sku not in all_skus)
    ]
    return SyncPlan(to_create, to_update, to_disable, unchanged, skipped)


def print_plan(plan: SyncPlan) -> None:
    print(
        f"Plan: {len(plan.to_create)} create, {len(plan.to_update)} update, "
        f"{len(plan.unchanged)} unchanged, {len(plan.to_disable)} disable, "
        f"{len(plan.skipped)} skipped",
        file=sys.stderr,
    )
    for payload in plan.to_create:
        tag = "" if payload.actiu else "  (disabled)"
        print(f"  CREATE  [{payload.sku}] {payload.preu:>7} €  {payload.nom}{tag}", file=sys.stderr)
    for old, payload in plan.to_update:
        changes = "; ".join(old.changes(payload))
        print(f"  UPDATE  [{payload.sku}] #{old.id} {payload.nom}", file=sys.stderr)
        print(f"            {changes}", file=sys.stderr)
    for existing in plan.to_disable:
        print(f"  DISABLE #{existing.id} [{existing.sku}] {existing.nom}", file=sys.stderr)
    for sku, nom, reason in plan.skipped:
        print(f"  SKIP    [{sku}] {reason}  {nom}", file=sys.stderr)


def summary_line(plan: SyncPlan) -> str:
    return (
        f"{len(plan.to_create)} created, {len(plan.to_update)} updated, "
        f"{len(plan.unchanged)} unchanged, {len(plan.to_disable)} disabled, "
        f"{len(plan.skipped)} skipped"
    )


def write_skipped(path: str, plan: SyncPlan) -> None:
    """Dump the skipped products to a CSV (sku, name, reason)."""
    import csv

    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["sku", "nom", "reason"])
        writer.writerows(plan.skipped)
    print(f"Wrote {len(plan.skipped)} skipped rows to {path}", file=sys.stderr)


def apply_plan(client: AlmaixeraClient, plan: SyncPlan) -> None:
    writes = plan.to_create + [payload for _, payload in plan.to_update]
    client.upsert_products(writes)
    if writes:
        print(
            f"  upserted {len(writes)} products "
            f"({len(plan.to_create)} new, {len(plan.to_update)} changed)",
            file=sys.stderr,
        )
    for existing in plan.to_disable:
        client.disable_product(existing.id)
        print(f"  disabled #{existing.id} [{existing.sku}]", file=sys.stderr)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("json_path", help="path to the ecoplaza products JSON")
    parser.add_argument("--base-url", default=ALMAIXERA_BASE)
    parser.add_argument("--user", default=DEFAULT_USER)
    parser.add_argument(
        "--password",
        default=os.environ.get("ALMAIXERA_PASSWORD", "enricus314"),
        help="admin password (or set ALMAIXERA_PASSWORD)",
    )
    parser.add_argument("--provider-id", type=int, default=DEFAULT_PROVIDER_ID)
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="only create/update the first N products (for exploration)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="show the plan without writing anything",
    )
    parser.add_argument(
        "--skipped-out",
        default=None,
        metavar="PATH",
        help="write the skipped products (sku, name, reason) to this CSV",
    )
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = _parse_args(argv)
    client = AlmaixeraClient(args.base_url, args.user, args.password)

    raw_products = load_raw_products(args.json_path)
    print(f"Loaded {len(raw_products)} products from {args.json_path}", file=sys.stderr)

    existing = client.list_provider_products(args.provider_id)
    print(
        f"Provider #{args.provider_id} currently has {len(existing)} products",
        file=sys.stderr,
    )

    plan = build_plan(raw_products, existing, args.provider_id, args.limit)
    print_plan(plan)

    if args.skipped_out is not None:
        write_skipped(args.skipped_out, plan)

    if args.dry_run:
        print(f"Dry run: no changes made. Plan: {summary_line(plan)}", file=sys.stderr)
        return 0

    apply_plan(client, plan)
    print(f"Sync complete: {summary_line(plan)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
