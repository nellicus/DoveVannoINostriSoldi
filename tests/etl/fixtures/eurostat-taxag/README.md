# Fixture Eurostat `gov_10a_taxag`

Questi input sono **sintetici** e servono esclusivamente ai test offline. Gli
importi non riproducono la serie pubblicata da Eurostat e non vanno citati come
dati reali.

`eurostat.json` è un documento JSON-stat 2.0 minimo con:

- l'ordine delle dimensioni **deliberatamente diverso** da quello della query
  canonica (`geo, time, na_item, unit, freq, sector`), così il parser non può
  affidarsi alla posizione;
- tre anni consecutivi, due aggregati e sei sottosettori;
- celle assenti per `S1312` in entrambi gli aggregati e per `S1314` nelle sole
  imposte, come nella fonte reale per l'Italia: servono a verificare che un
  importo assente resti assente e non diventi zero;
- componenti che riconciliano esattamente con il totale `S13_S212`.
