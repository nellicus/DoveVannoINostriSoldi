import snapshotJson from "@/data/generated/eurostat-taxag.json";
import {
  parseEurostatTaxagSnapshot,
  type EurostatTaxagSnapshot,
} from "@/lib/data/eurostat-taxag-contract";

const DAY = 86_400_000;
const STALE_AFTER_DAYS = 540;
const CENTRAL_SECTOR = "S1311";

let cachedSnapshot: EurostatTaxagSnapshot | undefined;

export class EurostatTaxagContractError extends Error {
  constructor(cause: unknown) {
    super("Lo snapshot delle entrate fiscali per sottosettore non supera il contratto dati", { cause });
    this.name = "EurostatTaxagContractError";
  }
}

export function getEurostatTaxagSnapshot(): EurostatTaxagSnapshot {
  if (cachedSnapshot) return cachedSnapshot;
  try {
    cachedSnapshot = parseEurostatTaxagSnapshot(snapshotJson);
    return cachedSnapshot;
  } catch (error) {
    throw new EurostatTaxagContractError(error);
  }
}

function freshness(referenceYear: number, now: Date) {
  const ageDays = Math.floor((now.getTime() - Date.parse(`${referenceYear}-12-31T00:00:00Z`)) / DAY);
  return {
    state: ageDays > STALE_AFTER_DAYS ? ("stale" as const) : ("fresh" as const),
    ageDays,
    staleAfterDays: STALE_AFTER_DAYS,
  };
}

export function getEurostatTaxagView(now = new Date()) {
  const snapshot = getEurostatTaxagSnapshot();
  const referenceYear = snapshot.years.at(-1)!;
  const sectorLabels = new Map(snapshot.sectors.map((sector) => [sector.code, sector]));

  const aggregates = snapshot.aggregates.map((aggregate) => {
    const latest = aggregate.series.at(-1)!;
    const central = latest.sectors.find((entry) => entry.code === CENTRAL_SECTOR);
    return {
      code: aggregate.code,
      label: aggregate.label,
      labelIt: aggregate.labelIt,
      denominator: aggregate.denominator,
      latest: {
        year: latest.year,
        total: latest.total,
        sectors: latest.sectors.map((entry) => ({
          ...entry,
          label: sectorLabels.get(entry.code)?.label ?? entry.code,
          labelIt: sectorLabels.get(entry.code)?.labelIt ?? entry.code,
        })),
      },
      // La quota centrale e il numero che verra citato: resta legata al proprio
      // denominatore, perche fra i due aggregati cambia di circa venticinque punti.
      centralShareBasisPoints: central?.shareBasisPoints ?? null,
      series: aggregate.series,
    };
  });

  return {
    ok: true as const,
    geo: snapshot.geo,
    referenceYear,
    years: snapshot.years,
    sectors: snapshot.sectors,
    totalSector: snapshot.totalSector,
    aggregates,
    comparison: {
      note: "Le due misure hanno denominatori diversi: non vanno confrontate fra loro ne sommate.",
      centralShareBasisPointsByAggregate: Object.fromEntries(
        aggregates.map((aggregate) => [aggregate.code, aggregate.centralShareBasisPoints]),
      ),
    },
    source: {
      owner: snapshot.source.owner,
      title: snapshot.source.title,
      datasetCode: snapshot.source.datasetCode,
      datasetUrl: snapshot.source.datasetUrl,
      apiUrl: snapshot.source.apiUrl,
      termsUrl: snapshot.source.termsUrl,
      licenseUrl: snapshot.source.termsUrl,
      attribution: "Eurostat, Main national accounts tax aggregates. Dati adattati da DoveVannoINostriSoldi.",
      cadence: snapshot.source.cadence,
      retrievedAt: snapshot.source.retrievedAt,
      accessedAt: snapshot.source.retrievedAt,
      upstreamUpdatedAt: snapshot.source.upstreamUpdatedAt,
    },
    measurement: {
      storedUnit: "centesimi di euro interi" as const,
      sourceUnit: snapshot.source.sourceUnit,
      transformation: snapshot.source.transformation,
      precisionNote:
        "Gli importi in euro esposti sono equivalenti convertiti da milioni, non misure osservate con precisione al centesimo.",
      shareNote:
        "Le quote sono ricalcolate dagli importi in punti base. L'unita PC_TOT pubblicata da Eurostat non e la quota fra sottosettori e non viene usata.",
    },
    freshness: freshness(referenceYear, now),
    caveats: snapshot.caveats,
  };
}

export type EurostatTaxagView = ReturnType<typeof getEurostatTaxagView>;
