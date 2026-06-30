# Beach Services NMB – nástroje

Sada jednoduchých skriptů (jediná závislost `requests`), které se přihlásí do
reálného API `api.beachservicesnmb.com` a pracují s rezervacemi. Vhodné i na
telefon (Pydroid 3 / Termux / a-Shell).

## Přihlášení

Vedle skriptů vytvoř soubor `.env` (vzor je v `.env.example`):

```
BEACH_EMAIL=tvuj@email.cz
BEACH_PASSWORD=tvojeheslo
```

Když `.env` chybí, skript se na e-mail a heslo zeptá ručně.

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
