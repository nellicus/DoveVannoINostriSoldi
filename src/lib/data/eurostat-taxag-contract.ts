import { z } from "zod";

const MAX_SAFE = Number.MAX_SAFE_INTEGER;
const SHARE_TOLERANCE_BASIS_POINTS = 5;
const SHARE_SUM_TOLERANCE_BASIS_POINTS = 20;

const money = z.number().int().min(-MAX_SAFE).max(MAX_SAFE);
const positiveMoney = money.positive();
const basisPoints = z.number().int().min(0).max(10_000);
const timestamp = z.string()
  .regex(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})$/, "timestamp ISO atteso")
  .refine((value) => Number.isFinite(Date.parse(value)), "timestamp ISO atteso");
const utcTimestamp = timestamp.refine((value) => value.endsWith("Z"), "timestamp UTC atteso");
const sha256 = z.string().regex(/^[0-9a-f]{64}$/);
const httpsUrl = z.url().refine((value) => new URL(value).protocol === "https:", "URL HTTPS atteso");
const officialUrl = (hostname: string, pathname: string) => httpsUrl.refine((value) => {
  const url = new URL(value);
  return url.hostname === hostname && url.pathname === pathname;
}, "URL ufficiale atteso");

function shareBasisPoints(numerator: number, denominator: number) {
  return Number((BigInt(numerator) * BigInt(10_000) + BigInt(denominator) / BigInt(2)) / BigInt(denominator));
}

function issue(context: z.RefinementCtx, message: string, path: PropertyKey[] = []) {
  context.addIssue({ code: "custom", message, path });
}

const sectorSchema = z.object({
  code: z.string().min(1),
  label: z.string().min(1),
  labelIt: z.string().min(1),
}).strict();

// Un importo assente resta assente: la fonte non pubblica la cella, e lo zero
// non e ammesso perche renderebbe indistinguibile "non esiste" da "vale niente".
const sectorAmountSchema = z.object({
  code: z.string().min(1),
  amount: positiveMoney.nullable(),
  shareBasisPoints: basisPoints.nullable(),
}).strict();

const seriesPointSchema = z.object({
  year: z.number().int().min(1900).max(2100),
  total: positiveMoney,
  sectors: z.array(sectorAmountSchema).min(2),
}).strict();

const aggregateSchema = z.object({
  code: z.string().min(1),
  label: z.string().min(1),
  labelIt: z.string().min(1),
  denominator: z.string().min(1),
  series: z.array(seriesPointSchema).min(1),
}).strict();

const sourceSchema = z.object({
  id: z.literal("eurostat"),
  owner: z.literal("Eurostat"),
  title: z.literal("Main national accounts tax aggregates"),
  datasetCode: z.literal("gov_10a_taxag"),
  datasetUrl: officialUrl("ec.europa.eu", "/eurostat/databrowser/view/gov_10a_taxag/default/table"),
  apiUrl: httpsUrl.refine((value) => {
    const url = new URL(value);
    return url.hostname === "ec.europa.eu"
      && url.pathname === "/eurostat/api/dissemination/statistics/1.0/data/gov_10a_taxag";
  }, "URL API ufficiale atteso"),
  termsUrl: officialUrl("ec.europa.eu", "/eurostat/web/main/help/copyright-notice"),
  retrievedAt: utcTimestamp,
  upstreamUpdatedAt: timestamp,
  cadence: z.literal("annuale"),
  sourceUnit: z.literal("milioni di euro"),
  transformation: z.string().min(1),
  bytes: z.number().int().positive().safe(),
  sha256,
}).strict();

export const eurostatTaxagSnapshotSchema = z.object({
  schemaVersion: z.literal(1),
  geo: z.object({ code: z.literal("IT"), label: z.string().min(1) }).strict(),
  unit: z.literal("euro_cents"),
  valueEncoding: z.literal("integer_cents"),
  years: z.array(z.number().int().min(1900).max(2100)).min(2),
  sectors: z.array(sectorSchema).min(2),
  totalSector: sectorSchema,
  // Due denominatori sono obbligatori: la quota contabilizzata al centro
  // cambia di circa venticinque punti fra imposte e imposte piu contributi,
  // quindi una misura sola non e pubblicabile senza il suo confronto.
  aggregates: z.array(aggregateSchema).min(2),
  source: sourceSchema,
  caveats: z.array(z.string().min(1)).min(1),
}).strict().superRefine((snapshot, context) => {
  const { years, sectors, totalSector, aggregates } = snapshot;

  if (years.some((year, index) => index > 0 && year - years[index - 1]! !== 1)) {
    issue(context, "anni non consecutivi", ["years"]);
  }

  const sectorCodes = sectors.map((sector) => sector.code);
  if (new Set(sectorCodes).size !== sectorCodes.length) {
    issue(context, "sottosettori duplicati", ["sectors"]);
  }
  if (sectorCodes.includes(totalSector.code)) {
    issue(context, "totale duplicato fra i sottosettori", ["totalSector"]);
  }

  const aggregateCodes = aggregates.map((aggregate) => aggregate.code);
  if (new Set(aggregateCodes).size !== aggregateCodes.length) {
    issue(context, "aggregati duplicati", ["aggregates"]);
  }

  aggregates.forEach((aggregate, aggregateIndex) => {
    const seriesYears = aggregate.series.map((point) => point.year);
    if (seriesYears.length !== years.length || seriesYears.some((year, index) => year !== years[index])) {
      issue(context, "anni della serie non allineati", ["aggregates", aggregateIndex, "series"]);
      return;
    }

    aggregate.series.forEach((point, pointIndex) => {
      const path = ["aggregates", aggregateIndex, "series", pointIndex] as PropertyKey[];
      const codes = point.sectors.map((entry) => entry.code);
      if (codes.length !== sectorCodes.length || codes.some((code, index) => code !== sectorCodes[index])) {
        issue(context, "sottosettori non allineati", [...path, "sectors"]);
        return;
      }

      let summed = 0;
      let shareSum = 0;
      point.sectors.forEach((entry, entryIndex) => {
        if (entry.amount === null) {
          if (entry.shareBasisPoints !== null) {
            issue(context, "quota senza importo", [...path, "sectors", entryIndex, "shareBasisPoints"]);
          }
          return;
        }
        if (entry.shareBasisPoints === null) {
          issue(context, "quota assente", [...path, "sectors", entryIndex, "shareBasisPoints"]);
          return;
        }
        // La quota e una derivazione: se non si ricalcola dall'importo, il
        // numero pubblicato non e piu ricostruibile e va rifiutato.
        if (Math.abs(entry.shareBasisPoints - shareBasisPoints(entry.amount, point.total)) > SHARE_TOLERANCE_BASIS_POINTS) {
          issue(context, "quota non coerente con l'importo", [...path, "sectors", entryIndex, "shareBasisPoints"]);
        }
        summed += entry.amount;
        shareSum += entry.shareBasisPoints;
      });

      if (summed !== point.total) {
        issue(context, "sottosettori non riconciliati", path);
      }
      if (Math.abs(shareSum - 10_000) > SHARE_SUM_TOLERANCE_BASIS_POINTS) {
        issue(context, "quote non sommano a cento", [...path, "sectors"]);
      }
    });
  });

  if (Date.parse(snapshot.source.upstreamUpdatedAt) > Date.parse(snapshot.source.retrievedAt)) {
    issue(context, "pubblicazione successiva all'osservazione", ["source", "upstreamUpdatedAt"]);
  }
});

export type EurostatTaxagSnapshot = z.infer<typeof eurostatTaxagSnapshotSchema>;
export type EurostatTaxagAggregate = EurostatTaxagSnapshot["aggregates"][number];

export function parseEurostatTaxagSnapshot(input: unknown): EurostatTaxagSnapshot {
  return eurostatTaxagSnapshotSchema.parse(input);
}
