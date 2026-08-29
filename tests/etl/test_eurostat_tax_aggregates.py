"""Offline contract tests for the Eurostat gov_10a_taxag snapshot."""

from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from scripts.etl import eurostat_tax_aggregates as ETL

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests/etl/fixtures/eurostat-taxag/eurostat.json"
LOCK_PATH = ROOT / "scripts/etl/specs/eurostat-taxag.source.json"
SNAPSHOT_PATH = ROOT / "src/data/generated/eurostat-taxag.json"

MILLION_TO_CENTS = 100_000_000


def fixture_lock() -> dict:
    """The committed lock, narrowed to the three years the fixture publishes."""
    lock = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    lock["eurostat"]["historyYears"] = 3
    return lock


def fixture_payload(mutate=None) -> bytes:
    document = json.loads(FIXTURE.read_text(encoding="utf-8"))
    if mutate is not None:
        mutate(document)
    return json.dumps(document, ensure_ascii=False).encode("utf-8")


class SourceLockTest(unittest.TestCase):
    def test_committed_lock_is_valid(self) -> None:
        ETL.validate_source_lock(json.loads(LOCK_PATH.read_text(encoding="utf-8")))

    def test_rejects_foreign_dataset(self) -> None:
        lock = fixture_lock()
        lock["eurostat"]["datasetCode"] = "gov_10a_main"
        with self.assertRaisesRegex(ETL.SnapshotError, "dataset Eurostat non autorizzato"):
            ETL.validate_source_lock(lock)

    def test_rejects_non_official_host(self) -> None:
        lock = fixture_lock()
        lock["eurostat"]["apiUrl"] = lock["eurostat"]["apiUrl"].replace("ec.europa.eu", "ec.europa.eu.evil.test")
        with self.assertRaisesRegex(ETL.SnapshotError, "host non consentito"):
            ETL.validate_source_lock(lock)

    def test_rejects_total_outside_the_declared_sectors(self) -> None:
        lock = fixture_lock()
        lock["eurostat"]["dimensions"]["sector"]["total"] = "S13"
        with self.assertRaisesRegex(ETL.SnapshotError, "sottosettore totale non incluso"):
            ETL.validate_source_lock(lock)


class ParseTest(unittest.TestCase):
    def test_parses_reordered_dimensions(self) -> None:
        parsed = ETL.parse_eurostat(fixture_payload(), fixture_lock())
        self.assertEqual(parsed["years"], [2023, 2024, 2025])
        self.assertEqual([item["code"] for item in parsed["aggregates"]], ["D2_D5_D91", "D2_D5_D91_D61_M_D995"])

        taxes = parsed["aggregates"][0]["series"][0]
        self.assertEqual(taxes["total"], 1000 * MILLION_TO_CENTS)
        by_code = {entry["code"]: entry for entry in taxes["sectors"]}
        self.assertEqual(by_code["S1311"]["amount"], 850 * MILLION_TO_CENTS)
        self.assertEqual(by_code["S1311"]["shareBasisPoints"], 8500)

    def test_absent_amounts_stay_absent(self) -> None:
        """S1312 has no Italian counterpart; it must never become a zero."""
        parsed = ETL.parse_eurostat(fixture_payload(), fixture_lock())
        for aggregate in parsed["aggregates"]:
            for point in aggregate["series"]:
                entry = next(item for item in point["sectors"] if item["code"] == "S1312")
                self.assertIsNone(entry["amount"])
                self.assertIsNone(entry["shareBasisPoints"])

    def test_shares_are_derived_not_copied(self) -> None:
        parsed = ETL.parse_eurostat(fixture_payload(), fixture_lock())
        for aggregate in parsed["aggregates"]:
            for point in aggregate["series"]:
                for entry in point["sectors"]:
                    if entry["amount"] is None:
                        continue
                    self.assertEqual(
                        entry["shareBasisPoints"], ETL.share_basis_points(entry["amount"], point["total"])
                    )

    def test_rejects_wrong_dataset_label(self) -> None:
        with self.assertRaisesRegex(ETL.SnapshotError, "dataset inatteso"):
            ETL.parse_eurostat(fixture_payload(lambda doc: doc.update(label="Something else")), fixture_lock())

    def test_rejects_unauthorised_sector_code(self) -> None:
        def mutate(doc: dict) -> None:
            category = doc["dimension"]["sector"]["category"]
            category["index"]["S13"] = category["index"].pop("S212")
            category["label"]["S13"] = category["label"].pop("S212")

        with self.assertRaisesRegex(ETL.SnapshotError, "dimensione sector: codici non autorizzati"):
            ETL.parse_eurostat(fixture_payload(mutate), fixture_lock())

    def test_rejects_relabelled_dimension(self) -> None:
        def mutate(doc: dict) -> None:
            doc["dimension"]["sector"]["category"]["label"]["S1311"] = "Administration centrale"

        with self.assertRaisesRegex(ETL.SnapshotError, "label non autorizzate"):
            ETL.parse_eurostat(fixture_payload(mutate), fixture_lock())

    def test_rejects_broken_reconciliation(self) -> None:
        def mutate(doc: dict) -> None:
            key = next(iter(doc["value"]))
            doc["value"][key] = doc["value"][key] + 1

        with self.assertRaisesRegex(ETL.SnapshotError, "non riconciliati|totale assente"):
            ETL.parse_eurostat(fixture_payload(mutate), fixture_lock())

    def test_rejects_non_consecutive_years(self) -> None:
        lock = fixture_lock()
        lock["eurostat"]["historyYears"] = 2

        def mutate(doc: dict) -> None:
            category = doc["dimension"]["time"]["category"]
            category["index"] = {"2023": 0, "2025": 1, "2027": 2}
            category["label"] = {"2023": "2023", "2025": "2025", "2027": "2027"}

        with self.assertRaisesRegex(ETL.SnapshotError, "anni non consecutivi"):
            ETL.parse_eurostat(fixture_payload(mutate), lock)

    def test_rejects_duplicate_json_keys(self) -> None:
        with self.assertRaisesRegex(ETL.SnapshotError, "chiave duplicata"):
            ETL.parse_eurostat(b'{"version":"2.0","version":"2.0"}', fixture_lock())

    def test_rejects_oversized_payload(self) -> None:
        lock = fixture_lock()
        lock["eurostat"]["maxResponseBytes"] = 1024
        with self.assertRaisesRegex(ETL.SnapshotError, "oltre il limite"):
            ETL.parse_eurostat(fixture_payload(), lock)

    def test_rejects_negative_amount(self) -> None:
        def mutate(doc: dict) -> None:
            key = next(iter(doc["value"]))
            doc["value"][key] = -1

        with self.assertRaisesRegex(ETL.SnapshotError, "negativo|non riconciliati"):
            ETL.parse_eurostat(fixture_payload(mutate), fixture_lock())

    def test_rejects_string_amount(self) -> None:
        def mutate(doc: dict) -> None:
            key = next(iter(doc["value"]))
            doc["value"][key] = "850"

        with self.assertRaisesRegex(ETL.SnapshotError, "valore non numerico"):
            ETL.parse_eurostat(fixture_payload(mutate), fixture_lock())


class SnapshotValidationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.snapshot = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))

    def test_committed_snapshot_is_valid(self) -> None:
        ETL.validate_snapshot(self.snapshot)

    def test_committed_snapshot_keeps_both_denominators(self) -> None:
        codes = [aggregate["code"] for aggregate in self.snapshot["aggregates"]]
        self.assertIn("D2_D5_D91", codes)
        self.assertIn("D2_D5_D91_D61_M_D995", codes)
        for aggregate in self.snapshot["aggregates"]:
            self.assertTrue(aggregate["denominator"].strip())

    def assert_invalid(self, mutate, pattern: str) -> None:
        candidate = copy.deepcopy(self.snapshot)
        mutate(candidate)
        with self.assertRaisesRegex(ETL.SnapshotError, pattern):
            ETL.validate_snapshot(candidate)

    def test_rejects_tampered_share(self) -> None:
        def mutate(snapshot: dict) -> None:
            entry = next(
                item for item in snapshot["aggregates"][0]["series"][0]["sectors"] if item["amount"] is not None
            )
            entry["shareBasisPoints"] += 100

        self.assert_invalid(mutate, "quota non coerente con l'importo")

    def test_rejects_broken_sector_sum(self) -> None:
        def mutate(snapshot: dict) -> None:
            snapshot["aggregates"][0]["series"][0]["total"] += MILLION_TO_CENTS

        self.assert_invalid(mutate, "non riconciliati|quota non coerente")

    def test_rejects_zero_substituted_for_absent(self) -> None:
        def mutate(snapshot: dict) -> None:
            entry = next(
                item for item in snapshot["aggregates"][0]["series"][0]["sectors"] if item["amount"] is None
            )
            entry["amount"] = 0
            entry["shareBasisPoints"] = 0

        self.assert_invalid(mutate, "importo zero non distinguibile da un dato assente")

    def test_rejects_single_denominator(self) -> None:
        self.assert_invalid(
            lambda snapshot: snapshot["aggregates"].pop(), "servono almeno due denominatori"
        )

    def test_rejects_missing_caveats(self) -> None:
        self.assert_invalid(lambda snapshot: snapshot.__setitem__("caveats", []), "caveat mancanti")

    def test_rejects_non_official_source_url(self) -> None:
        def mutate(snapshot: dict) -> None:
            snapshot["source"]["datasetUrl"] = "https://example.test/gov_10a_taxag"

        self.assert_invalid(mutate, "host non consentito")

    def test_rejects_bad_hash(self) -> None:
        self.assert_invalid(lambda snapshot: snapshot["source"].__setitem__("sha256", "x"), "hash non valido")

    def test_rejects_unsafe_integer(self) -> None:
        def mutate(snapshot: dict) -> None:
            snapshot["aggregates"][0]["series"][0]["total"] = ETL.MAX_SAFE_INTEGER + 1

        self.assert_invalid(mutate, "intero non sicuro")

    def test_rejects_gap_in_years(self) -> None:
        def mutate(snapshot: dict) -> None:
            snapshot["years"] = [snapshot["years"][0], snapshot["years"][-1]]

        self.assert_invalid(mutate, "anni non consecutivi")

    def test_rejects_publication_after_observation(self) -> None:
        def mutate(snapshot: dict) -> None:
            snapshot["source"]["upstreamUpdatedAt"] = "2099-01-01T00:00:00+02:00"

        self.assert_invalid(mutate, "pubblicazione successiva all'osservazione")


if __name__ == "__main__":
    unittest.main()
