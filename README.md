# Beach Services NMB – nástroje

Sada jednoduchých skriptů, které se přihlásí do reálného API
`api.beachservicesnmb.com` a pracují s rezervacemi. Vhodné i na telefon
(Pydroid 3 / Termux / a-Shell).

## Přihlášení

Vedle skriptů vytvoř soubor `.env` (vzor je v `.env.example`):

```
BEACH_EMAIL=tvuj@email.cz
BEACH_PASSWORD=tvojeheslo
```

Když `.env` chybí, skript se na e-mail a heslo zeptá ručně.

## Závislosti a řešení 403 (WAF)

Základní závislost je `requests`. Server (za **AWS load balancerem** – v odpovědi
`Server: awselb/2.0`, případně jinde Cloudflare) umí zablokovat „holé"
`python-requests` kvůli **TLS/JA3 fingerprintu** – typicky **403 Forbidden při
přihlášení z telefonu**, i když na PC stejný skript projde.

Skripty to řeší samy: login zkouší postupně víc přenosových backendů a ten,
který uspěje, použijí i pro další požadavky:

1. **`requests`** – holé requests (často projde na PC),
2. **`requests_tls`** – requests s upravenými TLS ciphery (jiný JA3), **čistě
   pythonový, bez nativních knihoven → funguje i v Pydroidu**,
3. **`curl_cffi`** – napodobí Chrome (TLS fingerprint + hlavičky), potřebuje binárku,
4. **`tls_client`** – záložní napodobovací knihovna.

Při neúspěchu login vypíše **celou surovou odpověď** (status, hlavičky, tělo,
WAF/proxy signály) a rozliší Cloudflare vs. AWS.

Na PC:

```
pip install requests curl_cffi
```

### Když to z telefonu (Pydroid) pořád padá na 403

Nejdřív zjisti, jestli jde o **fingerprint** (spraví napodobení prohlížeče), nebo
o **IP blok** (klientská knihovna nepomůže):

1. **Test přes prohlížeč na telefonu:** otevři `https://www.beachservicesnmb.com`
   na stejném připojení a zkus se přihlásit.
   - **Prohlížeč funguje, Python ne** → jde o TLS/JA3 → potřebuješ napodobení:
     v Pydroidu (menu → *Pip*) nainstaluj `curl_cffi`; kdyby nešlo, `tls_client`.
     (Backend `requests_tls` se zkouší automaticky a někdy stačí sám.)
   - **Prohlížeč taky nefunguje** → blok podle **IP/geo**. Zkus jinou síť
     (Wi-Fi ↔ mobilní data). Klientské knihovny to neobejdou.
2. **Když `curl_cffi`/`tls_client` nejdou v Pydroidu nainstalovat** (chtějí
   nativní binárku): použij **Termux** (`pkg install python`, pak
   `pip install curl_cffi`) – tam nativní závislosti obvykle projdou.

## Nástroje

- **`beach_mobil_1.py`** – výpis rezervací (začínající / končící) pro box a datum.
- **`umbrella_count.py`** – počet umbrell za měsíc (viz níže).

## `umbrella_count.py` – počet umbrell za měsíc (rozpad po týdnech)

Spočítá, kolik **umbrell** je v zadaném **měsíci** napříč **online** rezervacemi,
výsledek **rozepíše po ISO týdnech** (Po–Ne) a porovná zadané boxy.

### Co se počítá

Inventář má 3 typy položek: `combo`, `umbrella`, `chairs`.

- 1 combo = 1 umbrella
- 1 umbrella = 1 umbrella
- `chairs` se **nepočítají**

Příklad: rezervace `combo + umbrella` = 2 umbrelly.

Metrika je **umbrella-dny**: pro každý den se sečtou aktivní umbrelly, takže
combo objednané na 7 dní přispěje 7. To měří celkové vytížení boxu a umožňuje
srovnávat měsíce, týdny i boxy mezi sebou.

### Spuštění

```
python umbrella_count.py
```

Skript se postupně zeptá na:

1. **Boxy** – např. `43` nebo víc oddělených čárkou `42,43`
2. **Rok** a **měsíc**
3. **Jen online?** – `a` (jen `web_portal`) / `n` (všechny kanály)

E-mail a heslo se načtou automaticky z `.env` (hledá se ve složce skriptu i v
aktuální složce), takže je nemusíš psát.

Výstup: pro každý box **měsíční součet** umbrella-dnů a pod ním **rozpad po ISO
týdnech** (Po–Ne, ořezaných na měsíc – součet týdnů = měsíční součet). Když
zadáš víc boxů, na konci se vypíše porovnávací tabulka podle měsíčního součtu.

Příklad výstupu:

```
=== Box 43 ===
Měsíc celkem: 123 umbrella-dnů
  Týden 1  01.06.–07.06.  34
  Týden 2  08.06.–14.06.  30
  Týden 3  15.06.–21.06.  29
  Týden 4  22.06.–28.06.  22
  Týden 5  29.06.–30.06.   8
```

Zrušené rezervace se nepočítají.
