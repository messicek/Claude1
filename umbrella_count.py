"""Beach Services NMB – počet UMBRELL za ISO týden (mobilní verze).

Samostatný soubor, jediná závislost: `requests`. Po spuštění se postupně zeptá
na boxy, rok, měsíc a vybraný ISO týden (Po–Ne), pak spočítá, kolik umbrell je
v daném týdnu napříč ONLINE rezervacemi, a vypíše porovnání boxů.

== Co se počítá ==
  Inventář má 3 typy: combo, umbrella, chairs.
  Počítaná "umbrella" = combo + umbrella (každé × množství). Chairs se ignorují.
  Metrika je UMBRELLA-DNY: pro každý den týdne se sečtou aktivní umbrelly, takže
  combo objednané na celý týden přispěje 7 (= vytížení v týdnu).

== Jak to rozjet na telefonu (Android, Pydroid 3) ==
  1. Nainstaluj appku "Pydroid 3" z Obchodu Play.
  2. V Pydroidu otevři menu (vlevo nahoře) -> "Pip" -> nainstaluj "requests".
  3. Vedle tohoto souboru vytvoř textový soubor ".env" se dvěma řádky:
         BEACH_EMAIL=tvuj@email.cz
         BEACH_PASSWORD=tvojeheslo
     (Když .env nebude, skript se na e-mail a heslo jednou zeptá ručně.)
  4. Otevři tento soubor v Pydroidu a klikni na žlutou šipku "Run".

Logika i endpointy jsou shodné s ověřeným mobilním skriptem na výpis rezervací
(reálné API api.beachservicesnmb.com).
"""

import sys
from calendar import monthrange
from datetime import date, timedelta
from pathlib import Path

import requests

# ── Výchozí hodnoty (jen Enter = tyto) ──────────────────────────────────
BOX = "43"          # jeden box "43", nebo víc oddělených čárkou: "42,43"
JEN_ONLINE = True   # True = jen online (web_portal); False = všechny kanály
# ────────────────────────────────────────────────────────────────────────

API_URL = "https://api.beachservicesnmb.com"

BASE_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "Origin": "https://www.beachservicesnmb.com",
    "Referer": "https://www.beachservicesnmb.com/",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
}

CZ_MONTHS = [
    "", "leden", "únor", "březen", "duben", "květen", "červen",
    "červenec", "srpen", "září", "říjen", "listopad", "prosinec",
]


def load_credentials():
    """Načte BEACH_EMAIL/BEACH_PASSWORD z .env; co chybí, doptá se.

    Soubor .env se hledá ve složce skriptu i v aktuální pracovní složce – na
    telefonu (Pydroid) se totiž cesta ke skriptu může lišit od pracovní složky.
    Díky tomu se údaje načtou automaticky a nemusí se psát ručně.
    """
    creds = {}
    candidates = []
    try:
        candidates.append(Path(__file__).resolve().parent / ".env")
    except NameError:
        pass
    candidates.append(Path.cwd() / ".env")

    for env_path in candidates:
        if not env_path.exists():
            continue
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            creds.setdefault(key.strip(), val.strip().strip('"').strip("'"))
        break  # první nalezený .env vyhrává

    email = creds.get("BEACH_EMAIL") or input("E-mail: ").strip()
    password = creds.get("BEACH_PASSWORD") or input("Heslo: ").strip()
    return email, password


class BeachClient:
    """Tenký klient nad reálným API. Box se identifikuje přes inventory_id."""

    def __init__(self):
        self.session = requests.Session()
        self.token = None
        self._inventory = None

    def login(self, email, password):
        if not email or not password:
            raise EnvironmentError("Chybí e-mail nebo heslo.")
        r = self.session.post(
            f"{API_URL}/api/auth/login",
            json={"email": email, "password": password},
            headers=BASE_HEADERS,
            timeout=30,
        )
        r.raise_for_status()
        body = r.json()
        if not body.get("success"):
            raise RuntimeError(f"Přihlášení selhalo: {body}")
        self.token = body["data"]["accessToken"]

    def _headers(self):
        return dict(BASE_HEADERS, Authorization=f"Bearer {self.token}")

    def _get(self, path):
        r = self.session.get(API_URL + path, headers=self._headers(), timeout=30)
        r.raise_for_status()
        return r.json()

    def inventory(self):
        if self._inventory is None:
            self._inventory = self._get("/api/inventory")["data"]["data"]
        return self._inventory

    def resolve_box(self, box):
        """Číslo/název boxu -> (inventory_id, zobrazované jméno)."""
        import re

        target = str(box).strip().lower()
        target_named = target if target.startswith("box") else f"box {target}"
        items = self.inventory()

        for it in items:
            if str(it.get("name", "")).strip().lower() == target_named:
                return it["inventory_id"], it["name"]
        num = target.replace("box", "").strip()
        if num.isdigit():
            for it in items:
                nums = re.findall(r"\d+", str(it.get("name", "")))
                if num in nums:
                    return it["inventory_id"], it["name"]

        available = sorted(
            (str(it.get("name")) for it in items),
            key=lambda n: (len(n), n),
        )
        raise LookupError(
            f"Box '{box}' nenalezen. Dostupné boxy: {', '.join(available)}"
        )

    def booking_ids_on(self, inventory_id, day):
        data = self._get(
            f"/api/layout/{inventory_id}?date={day}"
            f"&variant=default&includeUnplaced=true"
        )["data"]
        ids = list(data.get("unplacedBookingIds", []))
        for pos in data.get("positions", []):
            bid = pos.get("bookingId") or pos.get("booking_id") or pos.get("id")
            if bid:
                ids.append(bid)
        return sorted(set(ids))

    def booking_full(self, inventory_id, booking_id):
        return self._get(
            f"/api/bookings/{inventory_id}/booking/{booking_id}"
            f"?scope=full&includeLocation=true"
        )["data"]


# ── Logika počítání umbrell ─────────────────────────────────────────────

def umbrella_count(rec):
    """Počet umbrell v jedné rezervaci: combo + umbrella (× quantity).

    Inventář má 3 typy (combo / umbrella / chairs). Combo i samostatná umbrella
    se počítají jako 1 umbrella za kus; chairs (a cokoli ostatního) se ignoruje.
    """
    total = 0
    for it in rec.get("booking_items") or rec.get("bookingItems") or []:
        bundle = it.get("product_bundle") or {}
        product = it.get("product") or {}
        name = (bundle.get("name") or product.get("name") or "").lower()
        if "combo" in name or "umbrella" in name:
            total += it.get("quantity") or 1
    return total


def booking_umbrellas(client, inventory_id, bid, only_online, cache):
    """Počet umbrell pro rezervaci s ohledem na stav a kanál (cachováno dle bid).

    Vrací 0 pro zrušené rezervace a (při only_online) pro neonline kanály.
    Stejná rezervace se v týdnu objeví ve více dnech – cache šetří API volání.
    """
    if bid in cache:
        return cache[bid]

    base = client._get(f"/api/bookings/{bid}")["data"]
    status = base.get("booking_status")
    channel = base.get("origin_channel") or ""

    if status == "canceled" or (only_online and channel != "web_portal"):
        cache[bid] = 0
        return 0

    real_inv = base.get("inventory_id") or inventory_id
    try:
        rec = client.booking_full(real_inv, bid)
    except requests.HTTPError:
        rec = base
    count = umbrella_count(rec)
    cache[bid] = count
    return count


def days_umbrella_total(client, inv_id, days, only_online, cache):
    """Součet umbrella-dnů pro daný seznam dat (objekty date) v jednom boxu."""
    total = 0
    for d in days:
        for bid in client.booking_ids_on(inv_id, d.isoformat()):
            total += booking_umbrellas(client, inv_id, bid, only_online, cache)
    return total


# ── Rozdělení měsíce na ISO týdny (Po–Ne) ───────────────────────────────

def iso_weeks_of_month(year, month):
    """Seznam (pondělí, neděle) všech Po–Ne týdnů, které zasahují do měsíce."""
    first = date(year, month, 1)
    last = date(year, month, monthrange(year, month)[1])
    monday = first - timedelta(days=first.weekday())  # pondělí týdne s 1. dnem
    weeks = []
    while monday <= last:
        weeks.append((monday, monday + timedelta(days=6)))
        monday += timedelta(days=7)
    return weeks


def month_weeks_clipped(year, month):
    """Rozpad měsíce po ISO týdnech, ořezaný na měsíc.

    Vrací seznam (label, [dny]) – dny jen v daném měsíci. Součet dnů přes
    všechny týdny = všechny dny měsíce, žádný den mimo měsíc.
    """
    weeks = []
    for mon, sun in iso_weeks_of_month(year, month):
        days = [
            mon + timedelta(days=i)
            for i in range(7)
            if (mon + timedelta(days=i)).month == month
            and (mon + timedelta(days=i)).year == year
        ]
        if not days:
            continue
        label = f"{days[0].strftime('%d.%m.')}–{days[-1].strftime('%d.%m.')}"
        weeks.append((label, days))
    return weeks


# ── Interaktivní vstup ──────────────────────────────────────────────────

def ask(prompt, default):
    """input() s výchozí hodnotou (prázdný vstup = default)."""
    val = input(f"{prompt} [{default}]: ").strip()
    return val or default


def ask_int(prompt, default, lo, hi):
    """Celé číslo v rozsahu <lo, hi>, prázdný vstup = default."""
    while True:
        raw = ask(prompt, str(default))
        try:
            val = int(raw)
        except ValueError:
            print(f"  Zadej číslo {lo}–{hi}.")
            continue
        if lo <= val <= hi:
            return val
        print(f"  Hodnota musí být {lo}–{hi}.")


def main():
    # Některé konzole (Windows cp1252) neumí diakritiku – přepni na UTF-8.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass

    print("=== Beach Services NMB – počet umbrell za měsíc ===\n")

    box = ask("Boxy (např. 43 nebo 42,43)", BOX)
    today = date.today()
    year = ask_int("Rok", today.year, 2000, 2100)
    month = ask_int("Měsíc (1–12)", today.month, 1, 12)
    only_online = ask("Jen online rezervace? (a/n)", "a" if JEN_ONLINE else "n")
    only_online = only_online.lower().startswith("a")

    try:
        email, password = load_credentials()
        client = BeachClient()
        client.login(email, password)
    except Exception as e:  # noqa: BLE001
        print(f"\n[CHYBA] {e}")
        return

    weeks = month_weeks_clipped(year, month)  # [(label, [dny])]
    kanal = "online" if only_online else "všechny kanály"
    print(f"\n##### {CZ_MONTHS[month].upper()} {year} – umbrella-dny ({kanal}) #####")

    cache = {}  # bid -> počet umbrell (sdíleno mezi boxy i dny)
    results = []
    for b in [x.strip() for x in box.split(",") if x.strip()]:
        try:
            inv_id, box_name = client.resolve_box(b)
        except (LookupError, requests.HTTPError) as e:
            print(f"\n[CHYBA] Box {b}: {e}")
            continue

        try:
            week_totals = [
                (label, days_umbrella_total(client, inv_id, days, only_online, cache))
                for label, days in weeks
            ]
        except requests.HTTPError as e:
            print(f"\n[CHYBA] Box {b}: {e}")
            continue

        month_total = sum(t for _, t in week_totals)
        print(f"\n=== {box_name} ===")
        print(f"Měsíc celkem: {month_total} umbrella-dnů")
        for i, (label, total) in enumerate(week_totals, 1):
            print(f"  Týden {i}  {label}  {total}")
        results.append((box_name, month_total))

    if len(results) > 1:
        print("\n=== Porovnání boxů (měsíc celkem) ===")
        width = max(len(name) for name, _ in results)
        for name, total in sorted(results, key=lambda r: r[1], reverse=True):
            print(f"  {name.ljust(width)}  {total}")


if __name__ == "__main__":
    main()
