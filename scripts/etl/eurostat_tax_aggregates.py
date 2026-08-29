#!/usr/bin/env python3
"""Build the verified snapshot of Eurostat `gov_10a_taxag` for Italy.

The dataset publishes tax aggregates by ESA 2010 institutional subsector. We
keep two aggregates on purpose, because the share accounted centrally depends
entirely on the denominator:

  * `D2_D5_D91` — taxes only;
  * `D2_D5_D91_D61_M_D995` — taxes plus social contributions.

For Italy 2023 the central subsector holds 85.6% of the first and 60.0% of the
second. Both figures are true and describe the same country, so neither may be
published without naming its denominator.

Only `unit=MIO_EUR` is requested. Eurostat also publishes `PC_TOT`, but that is
each subsector's share of its *own* total receipts, not the share between
subsectors: for Italy it reads 99.5 for central and 98.6 for local, which do not
sum to 100. Every share here is therefore derived from the amounts, in integer
basis points, and never taken from upstream.

Amounts absent upstream stay `null`. `S1312` (state government) has no Italian
counterpart and `S1314` receives contributions rather than taxes: those are not
zeroes and must not be rendered as such.

Stdlib only. Offline `--check` re-validates the committed artifact and never
touches the network or the working tree.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LOCK = ROOT / "scripts/etl/specs/eurostat-taxag.source.json"
DEFAULT_OUTPUT = ROOT / "src/data/generated/eurostat-taxag.json"

MAX_SAFE_INTEGER = 9_007_199_254_740_991
SHA256 = re.compile(r"^[0-9a-f]{64}$")
SHARE_TOLERANCE_BASIS_POINTS = 5

SECTOR_LABELS_IT = {
    "S13_S212": "Amministrazioni pubbliche e istituzioni dell'Unione europea",
    "S1311": "Amministrazioni centrali",
    "S1312": "Amministrazioni di livello statale federato",
    "S1313": "Amministrazioni locali",
    "S1314": "Enti di previdenza e assistenza sociale",
    "S212": "Istituzioni e organismi dell'Unione europea",
}

AGGREGATE_LABELS_IT = {
    "D2_D5_D91": "Imposte, senza contributi sociali",
    "D2_D5_D91_D61_M_D995": "Imposte e contributi sociali, al netto degli importi inesigibili",
}

AGGREGATE_DENOMINATORS_IT = {
    "D2_D5_D91": "imposte su produzione e importazioni, imposte correnti su reddito e patrimonio, imposte in conto capitale",
    "D2_D5_D91_D61_M_D995": "le stesse imposte piu i contributi sociali netti",
}

CAVEATS = [
    "La quota di un sottosettore e una attribuzione contabile: indica dove l'imposta e registrata, non dove il denaro resta.",
    "Fra amministrazioni esistono trasferimenti: una quota centrale alta non significa che la spesa avvenga al centro.",
    "Le due misure hanno denominatori diversi e non vanno confrontate fra loro ne sommate.",
    "Un importo assente resta assente: le Amministrazioni di livello statale federato non esistono in Italia e gli Enti di previdenza ricevono contributi, non imposte.",
    "I dati recenti di contabilita nazionale possono essere rivisti da Eurostat.",
]


class SnapshotError(ValueError):
    """Raised when an upstream or generated snapshot violates the contract."""


def _fail(message: str) -> None:
    raise SnapshotError(message)


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _require_dict(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        _fail(f"{label}: oggetto atteso")
    return value


def _require_list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        _fail(f"{label}: lista attesa")
    return value


def _exact_keys(value: dict[str, Any], expected: set[str], label: str) -> None:
    if set(value) != expected:
        _fail(f"{label}: chiavi inattese")


def _parse_timestamp(raw: Any, label: str) -> datetime:
    if not isinstance(raw, str) or not raw:
        _fail(f"{label}: timestamp atteso")
    normalized = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        _fail(f"{label}: timestamp non valido")
    if parsed.tzinfo is None:
        _fail(f"{label}: timestamp senza fuso orario")
    return parsed.astimezone(timezone.utc)


def _safe_integer(value: Any, label: str, *, nonnegative: bool = False) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        _fail(f"{label}: intero atteso")
    if abs(value) > MAX_SAFE_INTEGER:
        _fail(f"{label}: intero non sicuro per JavaScript")
    if nonnegative and value < 0:
        _fail(f"{label}: valore negativo")
    return value


def _decimal(raw: Any, label: str) -> Decimal:
    if not isinstance(raw, str) or not raw or raw.strip() != raw:
        _fail(f"{label}: valore numerico non valido")
    if not re.fullmatch(r"[+-]?\d+(?:[.,]\d+)?", raw):
        _fail(f"{label}: valore numerico non valido")
    try:
        value = Decimal(raw.replace(",", "."))
    except InvalidOperation:
        _fail(f"{label}: valore numerico non valido")
    if not value.is_finite():
        _fail(f"{label}: valore numerico non valido")
    return value


def money_millions_to_cents(raw: str, label: str) -> int:
    value = _decimal(raw, label)
    cents = value * Decimal(100_000_000)
    if cents != cents.to_integral_value():
        _fail(f"{label}: valore monetario non convertibile esattamente")
    result = int(cents)
    if result < 0:
        _fail(f"{label}: valore monetario negativo")
    return _safe_integer(result, label)


def share_basis_points(numerator: int, denominator: int) -> int:
    if denominator <= 0 or numerator < 0:
        _fail("quota: numeratore o denominatore non positivo")
    return int((Decimal(numerator) * 10_000 / Decimal(denominator)).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def validate_official_url(url: str, host: str, expected_path: str | None = None) -> urllib.parse.ParseResult:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https":
        _fail("URL: e richiesto HTTPS")
    if parsed.hostname != host or parsed.port not in (None, 443):
        _fail("URL: host non consentito")
    if parsed.username is not None or parsed.password is not None:
        _fail("URL: credenziali non consentite")
    if parsed.fragment:
        _fail("URL: fragment non consentito")
    if expected_path is not None and parsed.path != expected_path:
        _fail("URL: path non consentito")
    return parsed


def validate_redirect(original: str, target: str, host: str, expected_path: str | None = None) -> str:
    resolved = urllib.parse.urljoin(original, target)
    try:
        validate_official_url(resolved, host, expected_path)
    except SnapshotError as error:
        _fail(f"redirect non consentito: {error}")
    return resolved


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail(f"JSON: chiave duplicata {key}")
        result[key] = value
    return result


def _category_codes(dimension: dict[str, Any], name: str) -> tuple[list[str], dict[str, str]]:
    category = _require_dict(dimension.get("category"), f"dimensione {name}")
    index = category.get("index")
    labels = _require_dict(category.get("label"), f"label {name}")
    if isinstance(index, dict):
        if any(isinstance(position, bool) or not isinstance(position, int) for position in index.values()):
            _fail(f"dimensione {name}: indice non valido")
        ordered = sorted(index, key=index.get)
        if sorted(index.values()) != list(range(len(index))):
            _fail(f"dimensione {name}: cardinalita e indice non validi")
    elif isinstance(index, list) and all(isinstance(item, str) for item in index):
        ordered = index
    else:
        _fail(f"dimensione {name}: indice non valido")
    if set(labels) != set(ordered):
        _fail(f"dimensione {name}: label o cardinalita non valida")
    return ordered, labels


def validate_source_lock(lock: dict[str, Any]) -> None:
    _exact_keys(lock, {"schemaVersion", "eurostat"}, "source lock")
    if lock.get("schemaVersion") != 1:
        _fail("source lock: versione non supportata")
    euro = _require_dict(lock["eurostat"], "eurostat")
    _exact_keys(
        euro,
        {
            "owner", "title", "datasetCode", "datasetUrl", "apiUrl", "apiPath",
            "termsUrl", "allowedHost", "cadence", "maxResponseBytes", "dimensions", "historyYears",
        },
        "eurostat",
    )
    if euro.get("datasetCode") != "gov_10a_taxag":
        _fail("source lock: dataset Eurostat non autorizzato")
    host = euro.get("allowedHost", "")
    validate_official_url(euro.get("apiUrl", ""), host, euro.get("apiPath"))
    validate_official_url(euro.get("datasetUrl", ""), host)
    validate_official_url(euro.get("termsUrl", ""), host)

    dimensions = _require_dict(euro.get("dimensions"), "dimensioni Eurostat")
    if set(dimensions) != {"freq", "unit", "geo", "sector", "na_item"}:
        _fail("source lock: dimensioni Eurostat non autorizzate")
    for name, code in {"freq": "A", "unit": "MIO_EUR", "geo": "IT"}.items():
        if _require_dict(dimensions[name], name).get("code") != code:
            _fail("source lock: codici Eurostat non autorizzati")

    sector = _require_dict(dimensions["sector"], "sector")
    _exact_keys(sector, {"total", "codes", "labels"}, "sector")
    codes = _require_list(sector.get("codes"), "sector.codes")
    if len(codes) != len(set(codes)) or len(codes) < 2:
        _fail("source lock: sottosettori duplicati o insufficienti")
    if sector.get("total") not in codes:
        _fail("source lock: sottosettore totale non incluso")

    na_item = _require_dict(dimensions["na_item"], "na_item")
    _exact_keys(na_item, {"codes", "labels"}, "na_item")
    aggregates = _require_list(na_item.get("codes"), "na_item.codes")
    if len(aggregates) != len(set(aggregates)) or not aggregates:
        _fail("source lock: aggregati duplicati o assenti")
    for code in aggregates:
        if code not in AGGREGATE_LABELS_IT or code not in AGGREGATE_DENOMINATORS_IT:
            _fail("source lock: aggregato senza etichetta italiana")
    for code in codes:
        if code not in SECTOR_LABELS_IT:
            _fail("source lock: sottosettore senza etichetta italiana")

    history_years = euro.get("historyYears")
    if isinstance(history_years, bool) or not isinstance(history_years, int) or not 2 <= history_years <= 40:
        _fail("source lock: storia Eurostat non valida")
    max_bytes = euro.get("maxResponseBytes")
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or not 1024 <= max_bytes <= 4 * 1024 * 1024:
        _fail("source lock: limite risposta non valido")


def parse_eurostat(payload: bytes, lock: dict[str, Any]) -> dict[str, Any]:
    validate_source_lock(lock)
    euro = lock["eurostat"]
    dimensions = euro["dimensions"]
    sector_codes = list(dimensions["sector"]["codes"])
    total_sector = dimensions["sector"]["total"]
    aggregate_codes = list(dimensions["na_item"]["codes"])
    history_years = euro["historyYears"]

    if len(payload) > euro["maxResponseBytes"]:
        _fail("Eurostat: risposta oltre il limite")
    try:
        root = _require_dict(
            json.loads(payload.decode("utf-8"), object_pairs_hook=_reject_duplicate_pairs), "Eurostat"
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        _fail(f"Eurostat: JSON non valido: {error}")

    if root.get("version") != "2.0" or root.get("class") != "dataset":
        _fail("Eurostat: struttura JSON-stat inattesa")
    if root.get("label") != euro["title"] or root.get("source") != "ESTAT":
        _fail("Eurostat: dataset inatteso")

    ids = _require_list(root.get("id"), "Eurostat id")
    sizes = _require_list(root.get("size"), "Eurostat size")
    if len(ids) != len(set(ids)) or len(ids) != len(sizes):
        _fail("Eurostat: dimensioni incoerenti")
    if set(ids) != {"freq", "unit", "sector", "na_item", "geo", "time"}:
        _fail("Eurostat: dimensioni inattese")
    if any(isinstance(size, bool) or not isinstance(size, int) or size <= 0 for size in sizes):
        _fail("Eurostat: cardinalita non valida")

    dimension_root = _require_dict(root.get("dimension"), "Eurostat dimension")
    if set(dimension_root) != set(ids):
        _fail("Eurostat: dimensioni non allineate")

    ordered_codes: dict[str, list[str]] = {}
    for position, name in enumerate(ids):
        codes, labels = _category_codes(_require_dict(dimension_root[name], name), name)
        if len(codes) != sizes[position]:
            _fail(f"dimensione {name}: cardinalita non allineata")
        ordered_codes[name] = codes
        if name == "time":
            if any(not re.fullmatch(r"\d{4}", code) for code in codes):
                _fail("dimensione time: anno non valido")
            continue
        spec = dimensions[name]
        expected = list(spec.get("codes", [spec.get("code")]))
        if codes != expected:
            _fail(f"dimensione {name}: codici non autorizzati")
        expected_labels = spec.get("labels", {spec.get("code"): spec.get("label")})
        if labels != expected_labels:
            _fail(f"dimensione {name}: label non autorizzate")

    raw_values = root.get("value")
    total_cells = 1
    for size in sizes:
        total_cells *= size
    if isinstance(raw_values, list):
        if len(raw_values) != total_cells:
            _fail("Eurostat: valori non allineati")
        def value_at(position: int) -> Any:
            return raw_values[position]
    elif isinstance(raw_values, dict):
        if any(not re.fullmatch(r"\d+", key) or int(key) >= total_cells for key in raw_values):
            _fail("Eurostat: valori non allineati")
        def value_at(position: int) -> Any:
            return raw_values.get(str(position))
    else:
        _fail("Eurostat: valori assenti")

    strides: dict[str, int] = {}
    for position, name in enumerate(ids):
        stride = 1
        for size in sizes[position + 1:]:
            stride *= size
        strides[name] = stride

    years = sorted(ordered_codes["time"])
    if len(years) < history_years:
        _fail("Eurostat: storia insufficiente")
    selected = years[-history_years:]
    if [int(year) for year in selected] != list(range(int(selected[0]), int(selected[0]) + len(selected))):
        _fail("Eurostat: anni non consecutivi")

    def cell(sector: str, aggregate: str, year: str) -> Any:
        position = (
            ordered_codes["freq"].index(dimensions["freq"]["code"]) * strides["freq"]
            + ordered_codes["unit"].index(dimensions["unit"]["code"]) * strides["unit"]
            + ordered_codes["geo"].index(dimensions["geo"]["code"]) * strides["geo"]
            + ordered_codes["sector"].index(sector) * strides["sector"]
            + ordered_codes["na_item"].index(aggregate) * strides["na_item"]
            + ordered_codes["time"].index(year) * strides["time"]
        )
        return value_at(position)

    def amount(raw: Any, label: str) -> int | None:
        if raw is None:
            return None
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            _fail(f"{label}: valore non numerico")
        if isinstance(raw, float):
            if raw != raw or raw in (float("inf"), float("-inf")):
                _fail(f"{label}: valore non finito")
            text = format(raw, ".15g")
        else:
            text = str(raw)
        value = money_millions_to_cents(text, label)
        # Eurostat pubblica l'assenza come cella mancante, non come zero. Uno
        # zero renderebbe indistinguibili "non esiste" e "vale niente", che il
        # progetto tiene separati: meglio fermarsi e farlo esaminare.
        if value == 0:
            _fail(f"{label}: importo zero non distinguibile da un dato assente")
        return value

    components = [code for code in sector_codes if code != total_sector]
    aggregates: list[dict[str, Any]] = []
    for aggregate in aggregate_codes:
        series: list[dict[str, Any]] = []
        for year in selected:
            label = f"{aggregate} {year}"
            total = amount(cell(total_sector, aggregate, year), f"{label} totale")
            if total is None or total <= 0:
                _fail(f"{label}: totale assente o non positivo")
            entries = []
            for sector in components:
                value = amount(cell(sector, aggregate, year), f"{label} {sector}")
                entries.append({
                    "code": sector,
                    "amount": value,
                    "shareBasisPoints": None if value is None else share_basis_points(value, total),
                })
            if sum(entry["amount"] or 0 for entry in entries) != total:
                _fail(f"{label}: sottosettori non riconciliati")
            series.append({"year": int(year), "total": total, "sectors": entries})
        aggregates.append({
            "code": aggregate,
            "label": dimensions["na_item"]["labels"][aggregate],
            "labelIt": AGGREGATE_LABELS_IT[aggregate],
            "denominator": AGGREGATE_DENOMINATORS_IT[aggregate],
            "series": series,
        })

    _parse_timestamp(root.get("updated"), "Eurostat updated")
    return {
        "years": [int(year) for year in selected],
        "aggregates": aggregates,
        "upstreamUpdatedAt": root["updated"],
    }


def build_snapshot(lock: dict[str, Any], parsed: dict[str, Any], *, retrieved_at: str, raw: bytes) -> dict[str, Any]:
    validate_source_lock(lock)
    euro = lock["eurostat"]
    dimensions = euro["dimensions"]
    snapshot = {
        "schemaVersion": 1,
        "geo": {"code": dimensions["geo"]["code"], "label": "Italia"},
        "unit": "euro_cents",
        "valueEncoding": "integer_cents",
        "years": parsed["years"],
        "sectors": [
            {"code": code, "label": dimensions["sector"]["labels"][code], "labelIt": SECTOR_LABELS_IT[code]}
            for code in dimensions["sector"]["codes"]
            if code != dimensions["sector"]["total"]
        ],
        "totalSector": {
            "code": dimensions["sector"]["total"],
            "label": dimensions["sector"]["labels"][dimensions["sector"]["total"]],
            "labelIt": SECTOR_LABELS_IT[dimensions["sector"]["total"]],
        },
        "aggregates": parsed["aggregates"],
        "source": {
            "id": "eurostat",
            "owner": euro["owner"],
            "title": euro["title"],
            "datasetCode": euro["datasetCode"],
            "datasetUrl": euro["datasetUrl"],
            "apiUrl": euro["apiUrl"],
            "termsUrl": euro["termsUrl"],
            "retrievedAt": retrieved_at,
            "upstreamUpdatedAt": parsed["upstreamUpdatedAt"],
            "cadence": euro["cadence"],
            "sourceUnit": "milioni di euro",
            "transformation": "importi convertiti in centesimi interi; quote ricalcolate in punti base con arrotondamento half-up",
            "bytes": len(raw),
            "sha256": sha256_bytes(raw),
        },
        "caveats": list(CAVEATS),
    }
    validate_snapshot(snapshot)
    return snapshot


def validate_snapshot(snapshot: dict[str, Any]) -> None:
    _exact_keys(
        snapshot,
        {"schemaVersion", "geo", "unit", "valueEncoding", "years", "sectors", "totalSector", "aggregates", "source", "caveats"},
        "snapshot",
    )
    if snapshot.get("schemaVersion") != 1:
        _fail("snapshot: versione non supportata")
    if snapshot.get("unit") != "euro_cents" or snapshot.get("valueEncoding") != "integer_cents":
        _fail("snapshot: unita non supportata")

    years = _require_list(snapshot.get("years"), "years")
    if not years or any(isinstance(year, bool) or not isinstance(year, int) for year in years):
        _fail("snapshot: anni non validi")
    if years != list(range(years[0], years[0] + len(years))):
        _fail("snapshot: anni non consecutivi")

    sectors = _require_list(snapshot.get("sectors"), "sectors")
    codes = [_require_dict(sector, "sector").get("code") for sector in sectors]
    if not codes or len(codes) != len(set(codes)):
        _fail("snapshot: sottosettori duplicati")
    total_sector = _require_dict(snapshot.get("totalSector"), "totalSector")
    if total_sector.get("code") in codes:
        _fail("snapshot: totale duplicato fra i sottosettori")

    aggregates = _require_list(snapshot.get("aggregates"), "aggregates")
    if len(aggregates) < 2:
        _fail("snapshot: servono almeno due denominatori")
    seen: set[str] = set()
    for aggregate in aggregates:
        _exact_keys(_require_dict(aggregate, "aggregate"), {"code", "label", "labelIt", "denominator", "series"}, "aggregate")
        code = aggregate["code"]
        if code in seen:
            _fail("snapshot: aggregati duplicati")
        seen.add(code)
        if not aggregate["denominator"]:
            _fail(f"{code}: denominatore non dichiarato")
        series = _require_list(aggregate.get("series"), f"{code} series")
        if [point.get("year") for point in series] != years:
            _fail(f"{code}: anni della serie non allineati")
        for point in series:
            _exact_keys(_require_dict(point, "point"), {"year", "total", "sectors"}, f"{code} point")
            total = _safe_integer(point.get("total"), f"{code} {point.get('year')} totale", nonnegative=True)
            if total <= 0:
                _fail(f"{code} {point['year']}: totale non positivo")
            entries = _require_list(point.get("sectors"), "sectors")
            if [entry.get("code") for entry in entries] != codes:
                _fail(f"{code} {point['year']}: sottosettori non allineati")
            summed = 0
            share_total = 0
            for entry in entries:
                _exact_keys(_require_dict(entry, "entry"), {"code", "amount", "shareBasisPoints"}, "entry")
                amount = entry["amount"]
                share = entry["shareBasisPoints"]
                if amount is None:
                    if share is not None:
                        _fail(f"{code} {point['year']} {entry['code']}: quota senza importo")
                    continue
                _safe_integer(amount, f"{code} {point['year']} {entry['code']}", nonnegative=True)
                if amount == 0:
                    _fail(f"{code} {point['year']} {entry['code']}: importo zero non distinguibile da un dato assente")
                if share is None:
                    _fail(f"{code} {point['year']} {entry['code']}: quota assente")
                _safe_integer(share, f"{code} {point['year']} {entry['code']} quota", nonnegative=True)
                if share != share_basis_points(amount, total):
                    _fail(f"{code} {point['year']} {entry['code']}: quota non coerente con l'importo")
                summed += amount
                share_total += share
            if summed != total:
                _fail(f"{code} {point['year']}: sottosettori non riconciliati")
            if abs(share_total - 10_000) > SHARE_TOLERANCE_BASIS_POINTS:
                _fail(f"{code} {point['year']}: quote non sommano a cento")

    source = _require_dict(snapshot.get("source"), "source")
    _exact_keys(
        source,
        {
            "id", "owner", "title", "datasetCode", "datasetUrl", "apiUrl", "termsUrl",
            "retrievedAt", "upstreamUpdatedAt", "cadence", "sourceUnit", "transformation", "bytes", "sha256",
        },
        "source",
    )
    if source.get("id") != "eurostat" or source.get("datasetCode") != "gov_10a_taxag":
        _fail("source: provenienza inattesa")
    validate_official_url(source.get("datasetUrl", ""), "ec.europa.eu")
    validate_official_url(source.get("apiUrl", ""), "ec.europa.eu")
    validate_official_url(source.get("termsUrl", ""), "ec.europa.eu")
    retrieved = _parse_timestamp(source.get("retrievedAt"), "retrievedAt")
    if not str(source.get("retrievedAt", "")).endswith("Z"):
        _fail("retrievedAt: timestamp UTC atteso")
    upstream = _parse_timestamp(source.get("upstreamUpdatedAt"), "upstreamUpdatedAt")
    if upstream > retrieved:
        _fail("source: pubblicazione successiva all'osservazione")
    if not SHA256.fullmatch(str(source.get("sha256", ""))):
        _fail("source: hash non valido")
    _safe_integer(source.get("bytes"), "source bytes", nonnegative=True)

    caveats = _require_list(snapshot.get("caveats"), "caveats")
    if not caveats or any(not isinstance(item, str) or not item.strip() for item in caveats):
        _fail("snapshot: caveat mancanti")


class _RestrictedRedirect(urllib.request.HTTPRedirectHandler):
    def __init__(self, host: str, expected_path: str | None = None):
        self.host = host
        self.expected_path = expected_path

    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        safe_url = validate_redirect(request.full_url, new_url, self.host, self.expected_path)
        return super().redirect_request(request, file_pointer, code, message, headers, safe_url)


def _download(url: str, host: str, *, expected_path: str, max_bytes: int) -> bytes:
    validate_official_url(url, host, expected_path)
    opener = urllib.request.build_opener(_RestrictedRedirect(host, expected_path))
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": "DoveVannoINostriSoldi-ETL/1"},
        method="GET",
    )
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            with opener.open(request, timeout=20) as response:
                validate_official_url(response.geturl(), host, expected_path)
                if response.headers.get_content_type() not in {"application/json", "application/json-stat+json"}:
                    _fail("download: content type JSON inatteso")
                payload = response.read(max_bytes + 1)
                if len(payload) > max_bytes:
                    _fail("download: risposta oltre il limite")
                return payload
        except urllib.error.HTTPError as error:
            last_error = error
            if error.code not in {408, 425, 429, 500, 502, 503, 504} or attempt == 2:
                break
        except (urllib.error.URLError, TimeoutError) as error:
            last_error = error
            if attempt == 2:
                break
        time.sleep(2**attempt)
    _fail(f"download fallito: {last_error}")


def _load_json(path: Path) -> dict[str, Any]:
    try:
        return _require_dict(
            json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_reject_duplicate_pairs), str(path)
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        _fail(f"impossibile leggere {path}: {error}")


def write_snapshot_if_changed(path: Path, snapshot: dict[str, Any]) -> bool:
    payload = (json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    if path.exists() and path.read_bytes() == payload:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
    return True


def refresh(lock_path: Path, output_path: Path) -> bool:
    lock = _load_json(lock_path)
    validate_source_lock(lock)
    euro = lock["eurostat"]
    raw = _download(
        euro["apiUrl"], euro["allowedHost"], expected_path=euro["apiPath"], max_bytes=euro["maxResponseBytes"]
    )
    parsed = parse_eurostat(raw, lock)
    retrieved_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    if output_path.exists():
        previous = _load_json(output_path)
        validate_snapshot(previous)
        if previous["source"]["sha256"] == sha256_bytes(raw):
            retrieved_at = previous["source"]["retrievedAt"]
    snapshot = build_snapshot(lock, parsed, retrieved_at=retrieved_at, raw=raw)
    return write_snapshot_if_changed(output_path, snapshot)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lock", type=Path, default=DEFAULT_LOCK)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--check", action="store_true", help="Validate the committed lock and snapshot without network access")
    args = parser.parse_args()

    lock = _load_json(args.lock)
    validate_source_lock(lock)
    if args.check:
        snapshot = _load_json(args.output)
        validate_snapshot(snapshot)
        print(json.dumps({"valid": True, "years": snapshot["years"]}, separators=(",", ":")))
        return 0
    changed = refresh(args.lock, args.output)
    print(json.dumps({"changed": changed, "output": str(args.output)}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SnapshotError as error:
        print(f"errore: {error}", file=sys.stderr)
        sys.exit(1)
