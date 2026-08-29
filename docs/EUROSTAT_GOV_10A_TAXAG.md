# Imposte per sottosettore istituzionale (Eurostat `gov_10a_taxag`)

## Perimetro

Imposte italiane registrate nei conti dei sottosettori istituzionali SEC 2010,
annuali, ultimi dieci anni disponibili. Unità richiesta: `MIO_EUR`.

Sottosettori pubblicati:

| Codice | Sottosettore |
| --- | --- |
| `S1311` | Amministrazioni centrali |
| `S1312` | Amministrazioni di livello statale federato |
| `S1313` | Amministrazioni locali |
| `S1314` | Enti di previdenza e assistenza sociale |
| `S212` | Istituzioni e organismi dell'Unione europea |
| `S13_S212` | Totale: amministrazioni pubbliche e istituzioni UE |

`S1312` non ha corrispettivo italiano e resta assente in ogni anno. `S1314` è
assente sulle sole imposte, perché riceve contributi sociali e non imposte. Un
valore assente **non è uno zero**: il contratto rifiuta un importo pari a zero
proprio perché non sarebbe distinguibile da un dato mancante.

## Due denominatori, non uno

Lo snapshot conserva due aggregati:

| Codice | Che cosa misura |
| --- | --- |
| `D2_D5_D91` | Sole imposte: su produzione e importazioni, correnti su reddito e patrimonio, in conto capitale |
| `D2_D5_D91_D61_M_D995` | Le stesse imposte più i contributi sociali netti, al netto degli importi accertati e ritenuti inesigibili |

La quota contabilizzata dalle amministrazioni centrali nel 2025 vale **85,7%**
sul primo aggregato e **59,1%** sul secondo. La differenza non è un dettaglio
tecnico: i contributi sociali affluiscono in larga parte agli enti di
previdenza, quindi includerli sposta la quota centrale di oltre venti punti.

Per questo nessuna delle due misure viene pubblicata da sola, e ogni cifra
esposta dichiara il proprio denominatore.

## Che cosa il dato non misura

- Non dice dove il denaro **resta**: la quota di un sottosettore è
  un'attribuzione contabile, e fra amministrazioni esistono trasferimenti.
- Non misura autonomia fiscale, federalismo o capacità di spesa di un livello
  di governo.
- Non è una spesa: sono entrate registrate, non risorse impiegate.
- Le due misure non vanno sommate né confrontate fra loro.
- I dati recenti di contabilità nazionale possono essere rivisti da Eurostat.

## L'unità `PC_TOT` non è la quota fra sottosettori

Eurostat pubblica anche `unit=PC_TOT`. È la quota di ciascun sottosettore sul
**proprio** totale di entrate, non la ripartizione fra sottosettori: per
l'Italia vale 99,5 per le amministrazioni centrali, 98,6 per quelle locali e
100,0 per le istituzioni UE, valori che non sommano a cento.

L'ETL richiede quindi soltanto `MIO_EUR` e ricalcola ogni quota dagli importi,
in punti base con arrotondamento half-up. Il contratto TypeScript rifiuta una
quota che non si ricalcoli dal proprio importo, così il numero pubblicato resta
sempre ricostruibile.

## Fonti e source lock

- Dataset: [`gov_10a_taxag`](https://ec.europa.eu/eurostat/databrowser/view/gov_10a_taxag/default/table?lang=en)
- Titolare: Eurostat
- Condizioni di riuso: [copyright notice](https://ec.europa.eu/eurostat/web/main/help/copyright-notice)
- Source lock: `scripts/etl/specs/eurostat-taxag.source.json`

Il lock fissa codice del dataset, host e path ufficiali, dimensioni ammesse,
codici e **etichette upstream attese**: se Eurostat rinomina un sottosettore o
cambia un codice, la generazione si ferma invece di pubblicare un dato diverso
con la stessa forma.

## Riconciliazione

Per ogni aggregato e per ogni anno la somma dei sottosettori deve coincidere
**esattamente** con il totale `S13_S212`. La verifica è esatta, non
tollerante: gli importi convertiti in centesimi interi sono numeri esatti e in
tutti gli anni pubblicati la somma coincide alla cifra.

## Artefatti e aggiornamento

| Percorso | Contenuto |
| --- | --- |
| `scripts/etl/eurostat_tax_aggregates.py` | ETL, contratto fail-closed e `--check` offline |
| `scripts/etl/specs/eurostat-taxag.source.json` | source lock |
| `src/data/generated/eurostat-taxag.json` | snapshot verificato |
| `src/lib/data/eurostat-taxag-contract.ts` | contratto TypeScript |
| `src/lib/eurostat-taxag.ts` | modulo e vista condivisa |

Rigenerazione e verifica offline:

```bash
python3 scripts/etl/eurostat_tax_aggregates.py
python3 scripts/etl/eurostat_tax_aggregates.py --check
```

Il workflow `.github/workflows/eurostat-taxag-refresh.yml` è **di sola
verifica**: rigenera in un percorso temporaneo, confronta con l'artefatto
committato e segnala la differenza caricando il candidato. Non scrive sul
repository, perché un nuovo rilascio Eurostat può rivedere anche gli anni
passati e quella è una decisione sul significato dei dati, non un aggiornamento
di routine.

## Superficie pubblica

| Superficie | Percorso |
| --- | --- |
| Pagina | `/entrate-fiscali` |
| API | `GET /api/entrate-fiscali` |
| MCP | dataset `eurostat_entrate_sottosettore` |

La fonte è registrata come `eurostat`, la stessa che alimenta `gov_10a_main`
per il debito pubblico: stesso titolare, stessa cadenza annuale, stessa
politica di staleness. La scheda in `/fonti` copre entrambi i dataset e la
freschezza segue il **più vecchio** dei due, così un dataset aggiornato non
nasconde l'invecchiamento dell'altro.
