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

## `umbrella_count.py` – počet umbrell za ISO týden

Spočítá, kolik **umbrell** je v daném **ISO týdnu** (Po–Ne) napříč **online**
rezervacemi, a porovná zadané boxy.

### Co se počítá

Inventář má 3 typy položek: `combo`, `umbrella`, `chairs`.

- 1 combo = 1 umbrella
- 1 umbrella = 1 umbrella
- `chairs` se **nepočítají**

Příklad: rezervace `combo + umbrella` = 2 umbrelly.

Metrika je **umbrella-dny**: pro každý den týdne se sečtou aktivní umbrelly,
takže combo objednané na celý týden přispěje 7. To měří celkové vytížení boxu
v daném týdnu a umožňuje srovnávat týdny i boxy mezi sebou.

### Spuštění

```
python umbrella_count.py
```

Skript se postupně zeptá na:

1. **Boxy** – např. `43` nebo víc oddělených čárkou `42,43`
2. **Rok** a **měsíc**
3. **Týden** – vypíše Po–Ne týdny daného měsíce s konkrétními datumy, vybereš číslo
4. **Jen online?** – `a` (jen `web_portal`) / `n` (všechny kanály)

Výstup: pro každý box celkový počet umbrella-dnů + denní rozpad (Po–Ne) a na
konci porovnávací tabulka boxů seřazená sestupně.

Zrušené rezervace se nepočítají.
