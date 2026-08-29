import assert from "node:assert/strict";
import test from "node:test";
import "./helpers/register-ts-alias.mjs";

const { GET, createEurostatTaxagResponse } = await import("../src/app/api/entrate-fiscali/route.ts");
const { EurostatTaxagContractError } = await import("../src/lib/eurostat-taxag.ts");

test("GET /api/entrate-fiscali returns the shared view with public cache policy", async () => {
  const response = await GET();
  assert.equal(response.status, 200);
  assert.equal(response.headers.get("cache-control"), "public, max-age=3600, stale-while-revalidate=86400");
  const body = await response.json();
  assert.equal(body.ok, true);
  assert.equal(body.geo.code, "IT");
  assert.equal(body.aggregates.length, 2);
  assert.equal(body.source.licenseUrl, body.source.termsUrl);
  assert.match(body.measurement.transformation, /centesimi/);
});

test("the response keeps every share bound to its own denominator", async () => {
  const body = await (await GET()).json();
  for (const aggregate of body.aggregates) {
    assert.ok(aggregate.denominator.trim().length > 0, `${aggregate.code} senza denominatore`);
  }
  assert.match(body.comparison.note, /denominatori diversi/);
});

test("GET /api/entrate-fiscali fails closed with no-store when the contract is invalid", async () => {
  const response = createEurostatTaxagResponse(() => {
    throw new EurostatTaxagContractError(new Error("invalid fixture"));
  });
  assert.equal(response.status, 503);
  assert.equal(response.headers.get("cache-control"), "no-store");
  assert.deepEqual(await response.json(), { ok: false, error: "snapshot_contract_invalid" });
});
