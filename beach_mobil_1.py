"""Beach Services NMB – MOBILNÍ verze (pro telefon, Pydroid 3 / Termux / a-Shell).

Výpis rezervací. Po spuštění se zeptá na box, datum a režim (Enter = výchozí),
pak vypíše rezervace.

== Přihlášení proti WAF (Cloudflare) ==
Server před API stojí za Cloudflare/WAF, který umí zablokovat "holé"
python-requests kvůli TLS fingerprintu (typicky 403 z telefonu, i když na PC
requests projde). Login proto zkouší postupně víc přenosových backendů:

    1) requests     – nejrychlejší, funguje tam, kde WAF nevadí (často PC)
    2) curl_cffi    – napodobí Chrome (TLS fingerprint + hlavičky) → obejde WAF
    3) tls_client   – druhá napodobovací knihovna, když curl_cffi nejde

Backend, který u loginu uspěje, se použije i pro všechny další požadavky.
Při neúspěchu se vypíše CELÁ surová odpověď (status, hlavičky, tělo, cf-*).

== Jak to rozjet na telefonu (Android, Pydroid 3) ==
  1. Nainstaluj "Pydroid 3" z Obchodu Play.
  2. V Pydroidu menu -> "Pip" -> nainstaluj "requests" a "curl_cffi".
     (Když curl_cffi nejde nainstalovat, zkus "tls_client".)
  3. Vedle tohoto souboru vytvoř soubor ".env":
         BEACH_EMAIL=tvuj@email.cz
         BEACH_PASSWORD=tvojeheslo
  4. Otevři tento soubor v Pydroidu a klikni na žlutou šipku "Run".
"""

import re
import sys
from datetime import date, timedelta
from pathlib import Path

import requests

# ── Výchozí hodnoty (jen Enter = tyto) ──────────────────────────────────
BOX = "43"          # jeden box "43", nebo víc oddělených čárkou: "42,43"
REZIM = "start"     # "start" = začínající (vybavení/adresa/termín/pozn.)
                    # "end"   = končící (sběr vybavení) + telefon
JEN_ONLINE = True   # True = jen online (web_portal); False = všechny kanály
# ────────────────────────────────────────────────────────────────────────

API_URL = "https://api.beachservicesnmb.com"

# Origin/Referer musí odpovídat webu, ze kterého API normálně chodí požadavky.
# Známá funkční hodnota z desktopu je s "www"; kdyby WAF vadilo, zkus bez "www".
SITE = "https://www.beachservicesnmb.com"


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
    """Jednotné rozhraní nad requests / curl_cffi / tls_client.

    curl_cffi a tls_client napodobují prohlížeč (TLS fingerprint), což obejde
    WAF blokující holé python-requests. Všechny tři vracejí objekt odpovědi
    s .status_code, .headers, .text a .json().
    """

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


def load_credentials():
    """Načte BEACH_EMAIL/BEACH_PASSWORD z .env (složka skriptu i CWD); co chybí, doptá se."""
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
        break

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

    def booking_info(self, booking_id):
        data = self._get(f"/api/bookings/{booking_id}")["data"]
        cust = data.get("customer") or data.get("BookingCustomer") or {}
        name = cust.get("name") or "(bez jména)"
        phone = (cust.get("phone") or "").strip()
        dial = (cust.get("dial_code") or "").strip()
        if phone and dial:
            phone = f"{dial} {phone}"
        start_date = (data.get("start_date") or "")[:10]
        end_date = (data.get("end_date") or "")[:10]
        origin_channel = data.get("origin_channel") or ""
        return name, phone, start_date, end_date, origin_channel


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


def equip_summary(rec):
    counts = {}
    for it in rec.get("booking_items") or rec.get("bookingItems") or []:
        q = it.get("quantity") or 1
        bundle = it.get("product_bundle") or {}
        product = it.get("product") or {}
        name = bundle.get("name") or product.get("name") or "?"
        name = name[:1].upper() + name[1:]
        counts[name] = counts.get(name, 0) + q
    return ", ".join(f"{k} ×{v}" for k, v in counts.items())


def address_full(rec):
    """Celá adresa vyplněná zákazníkem při online objednávce, jinak ''."""
    md = rec.get("metadata") or {}
    af = md.get("autofillAddress") or {}
    return (af.get("fullAddress") or "").strip()


def next_day(day):
    """'2026-06-30' -> '2026-07-01'."""
    return (date.fromisoformat(day) + timedelta(days=1)).isoformat()


def list_names(client, box, day, mode="end", only_online=True):
    inv_id, box_name = client.resolve_box(box)
    tomorrow = next_day(day)
    ids = client.booking_ids_on(inv_id, day)
    if mode == "end":
        # Kdo končí ZÍTRA, je v zítřejším layoutu. Sjednotíme dnešní + zítřejší.
        ids = sorted(set(ids) | set(client.booking_ids_on(inv_id, tomorrow)))
    rows = []
    for bid in ids:
        if mode == "start":
            base = client._get(f"/api/bookings/{bid}")["data"]
            if base.get("booking_status") == "canceled":
                continue
            start_date = (base.get("start_date") or "")[:10]
            channel = base.get("origin_channel") or ""
            if start_date != day:
                continue
            if only_online and channel != "web_portal":
                continue
            real_inv = base.get("inventory_id") or inv_id
            try:
                rec = client.booking_full(real_inv, bid)
            except ApiError:
                rec = base
            end_date = (rec.get("end_date") or "")[:10]
            cust = rec.get("customer") or rec.get("BookingCustomer") or {}
            notes = " / ".join(
                x for x in (
                    (rec.get("notes") or "").strip(),
                    (rec.get("customer_notes") or "").strip(),
                ) if x
            )
            rows.append({
                "name": cust.get("name") or "(bez jména)",
                "equip": equip_summary(rec),
                "addr": address_full(rec),
                "start": start_date,
                "end": end_date,
                "notes": notes,
            })
        else:
            name, phone, start_date, end_date, channel = client.booking_info(bid)
            if end_date not in (day, tomorrow):
                continue
            if only_online and channel != "web_portal":
                continue
            rows.append({"name": name, "phone": phone, "end": end_date})
    return box_name, rows


def ask(prompt, default):
    """input() s výchozí hodnotou (prázdný vstup = default)."""
    val = input(f"{prompt} [{default}]: ").strip()
    return val or default


def main():
    # Některé konzole (Windows cp1252) neumí diakritiku – přepni na UTF-8.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass

    print("=== Beach Services NMB – výpis rezervací ===\n")

    box = ask("Box (např. 43 nebo 42,43)", BOX)
    day = ask("Datum (RRRR-MM-DD)", date.today().isoformat())
    rezim = ask("Režim (start/end)", REZIM).lower()
    if rezim not in ("start", "end"):
        rezim = REZIM

    try:
        email, password = load_credentials()
        client = BeachClient()
        client.login(email, password)
    except Exception as e:  # noqa: BLE001
        print(f"\n[CHYBA] {e}")
        return

    for b in [x.strip() for x in box.split(",") if x.strip()]:
        try:
            box_name, rows = list_names(client, b, day, mode=rezim, only_online=JEN_ONLINE)
        except (LookupError, ApiError) as e:
            print(f"\n[CHYBA] Box {b}: {e}")
            continue

        popis = "začínající" if rezim == "start" else "končící"
        kanal = " online" if JEN_ONLINE else ""
        print(f"\n=== {box_name} –{kanal} {popis} {day} ===")
        if rezim == "start":
            if not rows:
                print("(žádné rezervace)")
            for r in sorted(rows, key=lambda r: r["name"].casefold()):
                print(r["name"])
                if r["equip"]:
                    print(f"  vybavení: {r['equip']}")
                if r["addr"]:
                    print(f"  adresa:   {r['addr']}")
                print(f"  termín:   {r['start']} → {r['end']}")
                if r["notes"]:
                    print(f"  pozn.:    {r['notes']}")
            print(f"Celkem: {len(rows)}")
        else:
            tomorrow = next_day(day)
            zitra = sorted(
                (r for r in rows if r["end"] == tomorrow),
                key=lambda r: r["name"].casefold(),
            )
            dnes = sorted(
                (r for r in rows if r["end"] == day),
                key=lambda r: r["name"].casefold(),
            )
            print(f"\n-- Zítra poslední den ({tomorrow}) → poslat SMS --")
            if zitra:
                for r in zitra:
                    print(f"{r['name']} – {r['phone']}" if r["phone"] else r["name"])
            else:
                print("(nikdo)")
            print(f"\n-- Končí DNES ({day}) --")
            if dnes:
                for r in dnes:
                    print(f"{r['name']} – {r['phone']}" if r["phone"] else r["name"])
            else:
                print("(nikdo)")
            print(f"\nCelkem – dnes: {len(dnes)} | zítra: {len(zitra)}")


if __name__ == "__main__":
    main()
