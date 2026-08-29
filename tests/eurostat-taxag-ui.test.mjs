import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

async function source(path) {
  return readFile(new URL(path, import.meta.url), "utf8");
}

const page = await source("../src/app/entrate-fiscali/page.tsx");
const css = await source("../src/app/entrate-fiscali/entrate-fiscali.module.css");

test("the page stays a server component", () => {
  assert.doesNotMatch(page, /^"use client"/);
});

test("the title does not repeat the brand added by the layout", () => {
  const title = page.match(/title:\s*"([^"]*)"/)?.[1];
  assert.ok(title);
  assert.doesNotMatch(title, /dove\s*vanno\s*i\s*nostri\s*soldi/i);
});

test("both denominators are shown and each states its own", () => {
  assert.match(page, /Al centro, sulle sole imposte/);
  assert.match(page, /Al centro, con i contributi sociali/);
  assert.match(page, /Denominatore:\{?" ?"?\}?\s*\{aggregate\.denominator\}|Denominatore:/);
  assert.match(page, /data\.comparison\.note/);
});

test("the page explains why the two numbers differ instead of picking one", () => {
  assert.match(page, /Perche due numeri diversi/);
  assert.match(page, /Nessuna delle due e piu vera/);
});

test("an absent amount renders as not applicable, never as zero", () => {
  assert.match(page, /entry\.amount === null \? "Non applicabile"/);
  assert.match(page, /entry\.shareBasisPoints === null \? "Non applicabile"/);
  assert.doesNotMatch(page, /entry\.amount \?\? 0/);
});

test("every table header declares a scope", () => {
  for (const match of page.matchAll(/<th(\s[^>]*)?>/g)) {
    assert.match(match[0], /\sscope=/, `intestazione senza scope: ${match[0]}`);
  }
});

test("the scrollable table is a named region", () => {
  assert.match(page, /className=\{styles\.tableWrap\}[\s\S]{0,200}?role="region"/);
  assert.match(page, /aria-label=\{`Importi per sottosettore/);
  assert.match(page, /tabIndex=\{0\}/);
});

test("the decorative bar is hidden and mirrored by a real table", () => {
  assert.match(page, /className=\{styles\.bar\} aria-hidden="true"/);
  assert.match(page, /<table className="table">/);
});

test("external links open safely and say so", () => {
  const links = [...page.matchAll(/<a href=\{[^}]*\}[^>]*>/g)];
  assert.ok(links.length >= 3);
  for (const link of links) {
    assert.match(link[0], /target="_blank"/);
    assert.match(link[0], /rel="noreferrer"/);
  }
  assert.match(page, /si apre in una nuova scheda/);
});

test("provenance, transformation and caveats are all rendered", () => {
  assert.match(page, /data\.source\.datasetUrl/);
  assert.match(page, /data\.source\.licenseUrl/);
  assert.match(page, /data\.source\.attribution/);
  assert.match(page, /data\.measurement\.transformation/);
  assert.match(page, /data\.measurement\.shareNote/);
  assert.match(page, /data\.caveats\.map/);
});

test("a stale snapshot is disclosed rather than hidden", () => {
  assert.match(page, /data\.freshness\.state === "stale"/);
  assert.match(page, /warning-notice/);
});

test("the layout collapses on narrow viewports", () => {
  assert.match(css, /@media \(max-width: 640px\)/);
  assert.match(css, /\.tableWrap\s*\{[^}]*overflow-x:\s*auto/);
});
