import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import "./helpers/register-ts-alias.mjs";

const { parseEurostatTaxagSnapshot } = await import("../src/lib/data/eurostat-taxag-contract.ts");
const { getEurostatTaxagView } = await import("../src/lib/eurostat-taxag.ts");

const snapshot = JSON.parse(
  readFileSync(new URL("../src/data/generated/eurostat-taxag.json", import.meta.url), "utf8"),
);

function assertInvalid(mutator, pattern) {
  const candidate = structuredClone(snapshot);
  mutator(candidate);
  assert.throws(() => parseEurostatTaxagSnapshot(candidate), pattern);
}

function firstAmount(candidate) {
  return candidate.aggregates[0].series[0].sectors.find((entry) => entry.amount !== null);
}

function firstAbsent(candidate) {
  return candidate.aggregates[0].series[0].sectors.find((entry) => entry.amount === null);
}

test("the committed snapshot satisfies the contract", () => {
  assert.equal(parseEurostatTaxagSnapshot(snapshot).schemaVersion, 1);
});

test("both denominators stay published and each names its own", () => {
  const parsed = parseEurostatTaxagSnapshot(snapshot);
  const codes = parsed.aggregates.map((aggregate) => aggregate.code);
  assert.ok(codes.includes("D2_D5_D91"));
  assert.ok(codes.includes("D2_D5_D91_D61_M_D995"));
  for (const aggregate of parsed.aggregates) {
    assert.ok(aggregate.denominator.trim().length > 0);
  }
});

test("dropping a denominator is rejected", () => {
  assertInvalid((candidate) => candidate.aggregates.pop(), /aggregates/);
});

test("the two denominators disagree, which is why both are published", () => {
  const view = getEurostatTaxagView();
  const [taxes, withContributions] = view.aggregates;
  assert.notEqual(taxes.centralShareBasisPoints, withContributions.centralShareBasisPoints);
  // Non e una sfumatura: fra i due denominatori la quota centrale cambia di
  // oltre venti punti percentuali.
  assert.ok(Math.abs(taxes.centralShareBasisPoints - withContributions.centralShareBasisPoints) > 2_000);
});

test("a share that does not recompute from its amount is rejected", () => {
  assertInvalid((candidate) => {
    firstAmount(candidate).shareBasisPoints += 100;
  }, /quota non coerente con l'importo/);
});

test("sector amounts must reconcile with the total", () => {
  assertInvalid((candidate) => {
    candidate.aggregates[0].series[0].total += 100_000_000;
  }, /non riconciliati|quota non coerente/);
});

test("an absent amount cannot be replaced by a zero", () => {
  assertInvalid((candidate) => {
    const entry = firstAbsent(candidate);
    entry.amount = 0;
    entry.shareBasisPoints = 0;
  }, /amount/);
});

test("a share without an amount is rejected", () => {
  assertInvalid((candidate) => {
    firstAbsent(candidate).shareBasisPoints = 1_000;
  }, /quota senza importo/);
});

test("years must stay consecutive", () => {
  assertInvalid((candidate) => {
    candidate.years = [candidate.years[0], candidate.years.at(-1)];
  }, /anni/);
});

test("provenance must stay on the official host", () => {
  assertInvalid((candidate) => {
    candidate.source.datasetUrl = "https://example.test/gov_10a_taxag";
  }, /URL ufficiale atteso/);
});

test("a foreign dataset code is rejected", () => {
  assertInvalid((candidate) => {
    candidate.source.datasetCode = "gov_10a_main";
  }, /datasetCode/);
});

test("a malformed hash is rejected", () => {
  assertInvalid((candidate) => {
    candidate.source.sha256 = "x";
  }, /sha256/);
});

test("caveats cannot be emptied", () => {
  assertInvalid((candidate) => {
    candidate.caveats = [];
  }, /caveats/);
});

test("the view derives shares and never reads PC_TOT", () => {
  const view = getEurostatTaxagView();
  assert.equal(view.referenceYear, snapshot.years.at(-1));
  assert.match(view.measurement.shareNote, /PC_TOT/);
  for (const aggregate of view.aggregates) {
    for (const entry of aggregate.latest.sectors) {
      if (entry.amount === null) {
        assert.equal(entry.shareBasisPoints, null);
        continue;
      }
      const expected = Number(
        (BigInt(entry.amount) * 10_000n + BigInt(aggregate.latest.total) / 2n) / BigInt(aggregate.latest.total),
      );
      assert.equal(entry.shareBasisPoints, expected);
    }
  }
});
