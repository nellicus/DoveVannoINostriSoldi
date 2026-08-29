import type { Metadata } from "next";
import { getEurostatTaxagView } from "@/lib/eurostat-taxag";
import { compactEuro, exactEuro, longDate, percent } from "@/lib/format";
import styles from "./entrate-fiscali.module.css";

export const revalidate = 86_400;

export const metadata: Metadata = {
  title: "Dove sono contabilizzate le imposte",
  description:
    "Imposte italiane per sottosettore istituzionale, su due denominatori: sole imposte e imposte piu contributi sociali. Fonte Eurostat, con quote ricalcolate dagli importi.",
};

const euro = (cents: number) => cents / 100;
const bpPercent = (basisPoints: number) => percent(basisPoints / 100, 2);

export default function EntrateFiscaliPage() {
  const data = getEurostatTaxagView();
  const [taxesOnly, withContributions] = data.aggregates;

  return (
    <main className="shell page">
      <header className="page-intro">
        <span className={styles.kicker}>Snapshot verificato · Eurostat</span>
        <h1>Dove sono contabilizzate le imposte</h1>
        <p>
          Le imposte pagate in Italia sono registrate nei conti di sottosettori istituzionali diversi:
          amministrazioni centrali, amministrazioni locali, enti di previdenza e istituzioni dell&apos;Unione
          europea. Questa pagina mostra come si distribuiscono, e perche la stessa domanda ha due risposte
          legittime a seconda di che cosa si mette al denominatore.
        </p>
      </header>

      <div className={`stat-strip ${styles.periodStrip}`} aria-label="Periodo e quote principali">
        <span>
          <small>Anno di riferimento</small>
          {data.referenceYear}
        </span>
        <span>
          <small>Al centro, sulle sole imposte</small>
          {taxesOnly.centralShareBasisPoints === null ? "Non disponibile" : bpPercent(taxesOnly.centralShareBasisPoints)}
        </span>
        <span>
          <small>Al centro, con i contributi sociali</small>
          {withContributions.centralShareBasisPoints === null
            ? "Non disponibile"
            : bpPercent(withContributions.centralShareBasisPoints)}
        </span>
      </div>

      {data.freshness.state === "stale" ? (
        <p className="notice warning-notice">
          L&apos;ultimo anno pubblicato risale a oltre {data.freshness.staleAfterDays} giorni fa: Eurostat non ha ancora
          rilasciato un aggiornamento.
        </p>
      ) : null}

      <section className={`panel ${styles.section}`} aria-labelledby="due-numeri">
        <h2 className={styles.sectionTitle} id="due-numeri">
          Perche due numeri diversi
        </h2>
        <p>
          Le due misure contano cose diverse. La prima considera solo le imposte. La seconda aggiunge i contributi
          sociali, che in Italia affluiscono in larga parte agli enti di previdenza: aggiungendoli, la quota
          contabilizzata dalle amministrazioni centrali scende di oltre venti punti. Nessuna delle due e piu vera
          dell&apos;altra, ma nessuna delle due significa qualcosa senza il proprio denominatore.
        </p>
        <p className={styles.meta}>{data.comparison.note}</p>
      </section>

      {data.aggregates.map((aggregate) => {
        const anchor = `aggregato-${aggregate.code.toLowerCase()}`;
        return (
          <section className={`panel ${styles.section}`} key={aggregate.code} aria-labelledby={anchor}>
            <h2 className={styles.sectionTitle} id={anchor}>
              {aggregate.labelIt}
            </h2>
            <p>
              Totale {data.referenceYear}: <strong>{compactEuro(euro(aggregate.latest.total))}</strong>. Denominatore:{" "}
              {aggregate.denominator}.
            </p>

            <ul className={styles.barList} aria-label={`Quote per sottosettore, ${aggregate.labelIt}`}>
              {aggregate.latest.sectors.map((entry) => (
                <li key={entry.code}>
                  <div>
                    <strong>{entry.labelIt}</strong>
                    <span>{entry.shareBasisPoints === null ? "Non applicabile" : bpPercent(entry.shareBasisPoints)}</span>
                  </div>
                  <span className={styles.bar} aria-hidden="true">
                    <span style={{ width: `${(entry.shareBasisPoints ?? 0) / 100}%` }} />
                  </span>
                </li>
              ))}
            </ul>

            <div
              className={styles.tableWrap}
              role="region"
              aria-label={`Importi per sottosettore, ${aggregate.labelIt}`}
              tabIndex={0}
            >
              <table className="table">
                <thead>
                  <tr>
                    <th scope="col">Sottosettore</th>
                    <th scope="col" className="num">Importo</th>
                    <th scope="col" className="num">Quota</th>
                  </tr>
                </thead>
                <tbody>
                  {aggregate.latest.sectors.map((entry) => (
                    <tr key={entry.code}>
                      <th scope="row">{entry.labelIt}</th>
                      <td className="num">{entry.amount === null ? "Non applicabile" : exactEuro(euro(entry.amount))}</td>
                      <td className="num">
                        {entry.shareBasisPoints === null ? "Non applicabile" : bpPercent(entry.shareBasisPoints)}
                      </td>
                    </tr>
                  ))}
                  <tr>
                    <th scope="row">{data.totalSector.labelIt}</th>
                    <td className="num">{exactEuro(euro(aggregate.latest.total))}</td>
                    <td className="num">{percent(100, 2)}</td>
                  </tr>
                </tbody>
              </table>
            </div>

            <p className={styles.meta}>
              Anno {aggregate.latest.year} · unita: euro convertiti da milioni e percentuale del totale · formula:
              importo del sottosettore diviso totale ·{" "}
              <a href={data.source.datasetUrl} target="_blank" rel="noreferrer">
                Fonte: Eurostat {data.source.datasetCode} (si apre in una nuova scheda)
              </a>
            </p>
          </section>
        );
      })}

      <section className={styles.provenanceSection} aria-labelledby="fonti-originali">
        <h2 className={styles.sectionTitle} id="fonti-originali">
          Apri sempre il dato originale
        </h2>
        <p className={styles.meta}>
          {data.source.owner} · {data.source.title} · rilascio {data.source.cadence} · ultimo aggiornamento della fonte{" "}
          {longDate(data.source.upstreamUpdatedAt.slice(0, 10))} · osservato il {longDate(data.source.accessedAt.slice(0, 10))}
        </p>
        <p className={styles.provenanceActions}>
          <a href={data.source.datasetUrl} target="_blank" rel="noreferrer">
            Apri il dataset Eurostat (si apre in una nuova scheda)
          </a>{" "}
          ·{" "}
          <a href={data.source.licenseUrl} target="_blank" rel="noreferrer">
            Condizioni di riuso (si apre in una nuova scheda)
          </a>
        </p>
        <p className={styles.meta}>{data.measurement.transformation}</p>
        <p className={styles.meta}>{data.measurement.precisionNote}</p>
        <p className={styles.meta}>{data.measurement.shareNote}</p>
        <p className={styles.meta}>{data.source.attribution}</p>
        <ul className={styles.caveats}>
          {data.caveats.map((caveat) => (
            <li key={caveat}>{caveat}</li>
          ))}
        </ul>
      </section>
    </main>
  );
}
