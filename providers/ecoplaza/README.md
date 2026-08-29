# ecoplaza → almaixera

Keeps the almaixera products of the provider **Ecoplaza** in sync with the
ecoplaza store (`ecoplaza.b2binacatalog.com`, an
[inaCatalog](https://www.inacatalog.com/) B2B platform).

Two halves, one per file:

| File                     | Does                                                          |
| ------------------------ | ------------------------------------------------------------- |
| `scraper.py`             | extracts every product from ecoplaza into a raw JSON file      |
| `upload_to_almaixera.py` | reconciles that JSON against `/comandes/api/productes/`        |
| `sync.sh`                | runs the two in order                                          |
| `cron-sync.sh`           | what the weekly cron job inside the docker container runs      |

## Requirements

Python 3.9+ and nothing else — standard library only, no `pip install`. It does
**not** share Django's interpreter: the app runs on Python 3.7 and the docker
image carries a separate `python3` for this directory.

## Full migration in one command

`sync.sh` runs the whole pipeline. It takes the almaixera URL and `user:pass`;
any extra arguments are forwarded to `upload_to_almaixera.py`.

```bash
# extract everything and sync it into almaixera
./sync.sh http://localhost:5000 admin:enricus314

# preview only (no writes)
./sync.sh http://localhost:5000 admin:enricus314 --dry-run

# and dump the skipped products
./sync.sh http://localhost:5000 admin:enricus314 --skipped-out skipped.csv
```

Both positional arguments can be left out and passed as `ALMAIXERA_URL`,
`ALMAIXERA_USER` and `ALMAIXERA_PASSWORD` instead — which is how the cron job
runs it, so the password never appears in `ps`. `OUTPUT` overrides where the
intermediate `products.json` is written.

## Weekly run inside docker

The almaixera image ships `cron`, and `docker/entrypoint.sh` registers this
sync **only when a password is given** — a plain dev container never touches
the ecoplaza store:

```bash
docker/start.sh --mode prod --sync-password 'secret' rostoll-db.sql.gz
```

Default schedule is `0 1 * * 3` (Wednesday at 01:00, `Europe/Madrid`), settable
with `--sync-schedule`. Output goes to `/var/log/almaixera/ecoplaza-sync.log`
and to `docker logs`. See the *Sincronitzacio setmanal* section of the top-level
README for the full list of flags.

The two steps below are what `sync.sh` chains together, and can also be run on
their own.

## Extraction

```bash
# Full extraction (all ~3,785 products) -> products.json
python3 scraper.py

# Test run: only the first 10 products
python3 scraper.py --limit 10 --output sample.json
```

Options:

| Flag           | Default             | Description                                    |
| -------------- | ------------------- | ---------------------------------------------- |
| `--user`       | `Web-1598`          | Login user.                                    |
| `--password`   | *(built-in)*        | Login password.                                |
| `--output`     | `products.json`     | Where to write the JSON.                       |
| `--limit`      | *(none)*            | Only extract the first N products (testing).   |
| `--batch-size` | `200`               | Products requested per detail API call.        |

Progress is printed to `stderr`; the JSON goes to the output file.

## How it works

inaCatalog is a single-page app backed by a small set of JSON endpoints. The
scraper reproduces what the browser does:

1. **Login** — `POST /Usuario/Login`. Credentials are encoded as the site's JS
   does it: `"_enc_" + base64(value)` with `/` replaced by `_`. The session
   lives in the `.inaCatalog.Session` cookie.
2. **List codes** — `POST /Articulo/DameArticulosFiltroGlobal` returns every
   product code.
3. **Details** — `POST /Articulo/ObtenerArticulos/?t` with a JSON array of codes
   returns full product records, fetched in batches.

> **Note:** every inaCatalog data POST must carry a
> `Content-Type: application/x-www-form-urlencoded` (or `application/json`)
> header, otherwise the backend replies `400` with an empty body. The client
> handles this.

## Output shape

Products are written **raw** — exactly the key/value objects the backend
returns from `ObtenerArticulos`, with no transformation, renaming, or derived
fields. The document is:

```json
{
  "source": "https://ecoplaza.b2binacatalog.com",
  "count": 3785,
  "products": [ { /* raw inaCatalog product object, keys unchanged */ } ]
}
```

Each product object carries the backend's original fields, e.g. `codarticulo`
(SKU), `desarticulo` (name), `codean` (barcode), `obsarticulo` (description /
ingredients HTML), `descatalogo` / `desfamilia` / `dessubfamilia` (category),
`prearticulotarifa` / `prearticulotarifac` / `pvp` (prices), `tpciva` / `tpcre`
/ `tpcigic` (taxes), `stodisponible` (stock), `imagen` (relative image path),
`tags`, and more. Nothing is dropped or renamed.

## Type safety

The code is fully annotated and passes `mypy --strict`:

```bash
mypy --strict scraper.py upload_to_almaixera.py
```
