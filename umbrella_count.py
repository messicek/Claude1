"""Beach Services NMB – počet UMBRELL za měsíc (mobilní verze).

Samostatný soubor, jediná závislost: `requests`. Po spuštění se postupně zeptá
na boxy, rok, měsíc a jestli jen online, pak spočítá, kolik umbrell je v daném
měsíci napříč rezervacemi, výsledek rozepíše po ISO týdnech (Po–Ne) a porovná boxy.

== Co se počítá ==
  Inventář má 3 typy: combo, umbrella, chairs.
  Počítaná "umbrella" = combo + umbrella (každé × množství). Chairs se ignorují.
  Metrika je UMBRELLA-DNY: pro každý den se sečtou aktivní umbrelly, takže
  combo objednané na 7 dní přispěje 7 (= vytížení).

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

# Origin/Referer musí odpovídat webu, ze kterého API normálně chodí požadavky.
# Známá funkční hodnota z desktopu je s "www"; kdyby WAF vadilo, zkus bez "www".
SITE = "https://www.beachservicesnmb.com"

CZ_MONTHS = [
    "", "leden", "únor", "březen", "duben", "květen", "červen",
    "červenec", "srpen", "září", "říjen", "listopad", "prosinec",
]


def browser_headers(for_impersonation=False):
    """Plné "browser" hlavičky pro login/GET.

    Při napodobování prohlížeče (curl_cffi/tls_client) NEposíláme vlastní
    User-Agent – necháme knihovnu nastavit UA odpovídající jejímu TLS
    fingerprintu, ať je request konzistentní.
    """
    h = {
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "cs-CZ,cs;q=0.9",
        "Content-Type": "application/json",
        "Origin": SITE,
        "Referer": SITE + "/",
    }
    if not for_impersonation:
        h["User-Agent"] = (
            "Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0 Mobile Safari/537.36"
        )
    return h


class ApiError(Exception):
    """HTTP chyba (status >= 400) nezávislá na použitém backendu."""

    def __init__(self, status, text, headers=None):
        super().__init__(f"HTTP {status}")
        self.status = status
        self.text = text or ""
        self.headers = dict(headers or {})


# ── Přenosová vrstva s fallbackem ───────────────────────────────────────
# Server (AWS ALB + nginx / příp. Cloudflare) umí zablokovat "holé"
# python-requests kvůli TLS fingerprintu (typicky 403 z telefonu). Login proto
# zkouší postupně requests -> requests_tls (pythonová úprava JA3) -> curl_cffi
# (napodobí Chrome) -> tls_client; úspěšný backend se použije i pro další requesty.

def _browserish_session():
    """requests.Session s upravenými TLS ciphery (Chrome-like).

    Změní JA3 fingerprint oproti výchozímu python-requests BEZ jakékoli
    nativní knihovny – funguje i v Pydroidu. Pomůže tam, kde WAF blokuje
    default-python fingerprint (ne dokonalá náhrada prohlížeče, ale často stačí).
    """
    import ssl
    from requests.adapters import HTTPAdapter

    ciphers = (
        "ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:"
        "ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384:"
        "ECDHE-ECDSA-CHACHA20-POLY1305:ECDHE-RSA-CHACHA20-POLY1305:"
        "ECDHE-RSA-AES128-SHA:ECDHE-RSA-AES256-SHA:"
        "AES128-GCM-SHA256:AES256-GCM-SHA384:AES128-SHA:AES256-SHA"
    )
    ctx = ssl.create_default_context()
    ctx.set_ciphers(ciphers)
    try:
        ctx.set_alpn_protocols(["h2", "http/1.1"])
    except NotImplementedError:
        pass

    class _TlsAdapter(HTTPAdapter):
        def init_poolmanager(self, *a, **kw):
            kw["ssl_context"] = ctx
            return super().init_poolmanager(*a, **kw)

        def proxy_manager_for(self, *a, **kw):
            kw["ssl_context"] = ctx
            return super().proxy_manager_for(*a, **kw)

    s = requests.Session()
    s.mount("https://", _TlsAdapter())
    return s


class Transport:
    """Jednotné rozhraní nad requests / requests_tls / curl_cffi / tls_client."""

    def __init__(self, backend):
        self.backend = backend
        if backend == "requests":
            self._s = requests.Session()
        elif backend == "requests_tls":
            self._s = _browserish_session()
        elif backend == "curl_cffi":
            from curl_cffi import requests as cffi
            self._s = cffi.Session(impersonate="chrome")
        elif backend == "tls_client":
            import tls_client
            self._s = tls_client.Session(
                client_identifier="chrome_120",
                random_tls_extension_order=True,
            )
        else:
            raise ValueError(f"Neznámý backend: {backend}")

    @staticmethod
    def available():
        """Seznam dostupných backendů v pořadí preference.

        requests_tls je čistě pythonový (žádné nativní knihovny) → funguje i v
        Pydroidu; zkusí se hned po holém requests.
        """
        backends = ["requests", "requests_tls"]
        try:
            import curl_cffi  # noqa: F401
            backends.append("curl_cffi")
        except Exception:
            pass
        try:
            import tls_client  # noqa: F401
            backends.append("tls_client")
        except Exception:
            pass
        return backends

    def _headers(self, extra):
        # curl_cffi/tls_client si nastaví vlastní browser UA odpovídající JA3;
        # requests a requests_tls potřebují náš browser UA.
        own_ua = self.backend in ("curl_cffi", "tls_client")
        h = browser_headers(for_impersonation=own_ua)
        if extra:
            h.update(extra)
        return h

    def get(self, url, headers=None, timeout=30):
        h = self._headers(headers)
        if self.backend == "tls_client":
            return self._s.get(url, headers=h)  # tls_client řeší timeout jinak
        return self._s.get(url, headers=h, timeout=timeout)

    def post(self, url, json_body, headers=None, timeout=30):
        h = self._headers(headers)
        if self.backend == "tls_client":
            return self._s.post(url, headers=h, json=json_body)
        return self._s.post(url, headers=h, json=json_body, timeout=timeout)


def dump_response(r):
    """Vypíše CELOU surovou odpověď serveru (diagnostika WAF/proxy)."""
    hdrs = dict(r.headers)
    print("  STATUS:", r.status_code)
    print("  --- HLAVIČKY ---")
    for k, v in hdrs.items():
        print(f"  {k}: {v}")
    signal = {k: v for k, v in hdrs.items()
              if k.lower() in ("server", "cf-ray", "cf-mitigated", "cf-cache-status",
                               "x-amzn-waf-action", "x-amzn-requestid", "x-amz-cf-id", "via")}
    print("  --- WAF / PROXY SIGNÁLY ---")
    print("  ", signal if signal else "(žádné)")
    print("  --- TĚLO (prvních 1500 znaků) ---")
    print("  " + (r.text or "")[:1500].replace("\n", "\n  "))
    srv = str(hdrs.get("Server") or hdrs.get("server") or "").lower()
    lower = {k.lower() for k in hdrs}
    if "cf-ray" in lower or "cloudflare" in srv:
        print("  >>> Cloudflare WAF (cf-ray / Server: cloudflare).")
    elif "awselb" in srv or "cloudfront" in srv or "x-amzn-waf-action" in lower:
        print("  >>> AWS (ALB/WAF) blokuje request – NE Cloudflare.")
        print("      Typicky IP reputace/geo nebo TLS/JA3 fingerprint klienta.")


def _login_failed_help(backends, last_status):
    lines = [
        "Přihlášení selhalo přes všechny dostupné backendy "
        f"({', '.join(backends)}); poslední status: {last_status}.",
    ]
    have_impersonation = any(b in backends for b in ("curl_cffi", "tls_client"))
    if not have_impersonation:
        lines.append(
            "Zkoušely se jen pythonové backendy (requests, requests_tls). Pokud jde "
            "o TLS/JA3 blok, nainstaluj v Pydroidu (menu -> Pip) 'curl_cffi' "
            "(napodobí Chrome). Kdyby curl_cffi nešlo, zkus 'tls_client'; kdyby "
            "ani to ne, viz README (Termux / test přes prohlížeč)."
        )
    else:
        lines.append(
            "I napodobovací backend (curl_cffi/tls_client) byl odmítnut → nejspíš to "
            "NENÍ TLS fingerprint, ale blok podle IP/geo. Ověř přes prohlížeč na "
            "telefonu (viz README) a zkus jinou síť (Wi-Fi <-> mobilní data)."
        )
    return "\n".join(lines)


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
        self.transport = None
        self.token = None
        self._inventory = None

    def login(self, email, password):
        if not email or not password:
            raise EnvironmentError("Chybí e-mail nebo heslo.")

        payload = {"email": email, "password": password}
        backends = Transport.available()
        print(f"Dostupné přenosové backendy: {', '.join(backends)}")
        last_status = None

        for backend in backends:
            print(f"\n>>> Zkouším přihlášení přes backend: {backend}")
            try:
                t = Transport(backend)
            except Exception as e:  # noqa: BLE001
                print(f"  [{backend}] nelze inicializovat: {type(e).__name__}: {e}")
                continue
            try:
                r = t.post(f"{API_URL}/api/auth/login", payload)
            except Exception as e:  # noqa: BLE001
                print(f"  [{backend}] síťová chyba: {type(e).__name__}: {e}")
                continue

            if r.status_code == 200:
                try:
                    body = r.json()
                except Exception:  # noqa: BLE001
                    body = None
                if body and body.get("success"):
                    self.transport = t
                    self.token = body["data"]["accessToken"]
                    print(f"  [{backend}] ✅ přihlášení OK. Token: {self.token[:16]}…")
                    print("  (tento backend se použije i pro další požadavky)")
                    return
                print(f"  [{backend}] status 200, ale neočekávané tělo:")
                dump_response(r)
            else:
                print(f"  [{backend}] ❌ přihlášení selhalo:")
                dump_response(r)
            last_status = r.status_code

        raise RuntimeError(_login_failed_help(backends, last_status))

    def _get(self, path):
        url = API_URL + path
        r = self.transport.get(url, headers={"Authorization": f"Bearer {self.token}"})
        if r.status_code >= 400:
            raise ApiError(r.status_code, r.text, r.headers)
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
    except ApiError:
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
        except (LookupError, ApiError) as e:
            print(f"\n[CHYBA] Box {b}: {e}")
            continue

        try:
            week_totals = [
                (label, days_umbrella_total(client, inv_id, days, only_online, cache))
                for label, days in weeks
            ]
        except ApiError as e:
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
