"""Schnuppertag-Erinnerung: sendet Betreuer:innen eine Sammelmail mit allen
Moodle-Anmeldungen, deren Status noch dem Trigger (z.B. "_Neu") entspricht.

Aufruf:
    python notify.py               # echte Mails senden
    python notify.py --dry-run     # nur anzeigen, nichts senden
    python notify.py --to-me ADDR  # alle Mails an ADDR umleiten (Test)
"""
from __future__ import annotations

import argparse
import csv
import html
import logging
import os
import re
import smtplib
import sys
import urllib.request
from datetime import date

try:
    import tomllib  # Python >= 3.11
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # pip install tomli
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from email.message import EmailMessage
from html.parser import HTMLParser
from pathlib import Path

BASE = Path(__file__).resolve().parent
log = logging.getLogger("notify")


# ---------------------------------------------------------------- config ----
def load_env(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


def load_config(path: Path) -> dict:
    with path.open("rb") as f:
        cfg = tomllib.load(f)
    load_env(BASE / ".env")
    return cfg


# ------------------------------------------------------------------ feed ----
class CellParser(HTMLParser):
    """Sammelt den Text jeder <td>-Zelle in Reihenfolge."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.cells: list[str] = []
        self._buf: list[str] | None = None
        self._depth = 0

    def handle_starttag(self, tag, attrs):
        if tag == "td":
            if self._depth == 0:
                self._buf = []
            self._depth += 1
        elif tag in ("br", "p", "div") and self._buf is not None:
            self._buf.append("\n")

    def handle_endtag(self, tag):
        if tag == "td" and self._depth:
            self._depth -= 1
            if self._depth == 0 and self._buf is not None:
                text = "".join(self._buf)
                text = re.sub(r"[ \t\xa0]+", " ", text)
                text = re.sub(r"\s*\n\s*", "\n", text).strip()
                self.cells.append(text)
                self._buf = None

    def handle_data(self, data):
        if self._buf is not None:
            self._buf.append(data)


def parse_description(desc_html: str) -> dict[str, str]:
    p = CellParser()
    p.feed(desc_html)
    fields: dict[str, str] = {}
    cells = p.cells
    for i, cell in enumerate(cells[:-1]):
        if cell.endswith(":") and "\n" not in cell:
            fields[cell[:-1].strip()] = html.unescape(cells[i + 1]).strip()
    return fields


# Auswahl "Master" ist im Schnuppertag nicht vorgesehen -> wird ignoriert.
IGNORED_STUDIENGAENGE = {"MASTER"}


@dataclass
class Entry:
    rid: str
    school: str
    link: str
    date: str
    fields: dict[str, str] = field(default_factory=dict)

    def get(self, key: str) -> str:
        return self.fields.get(key, "")

    @property
    def name(self) -> str:
        return f"{self.get('Vorname')} {self.get('Nachname')}".strip()

    @property
    def studiengaenge(self) -> list[str]:
        return [s for s in (self.get("Studiengang Wahl 1"), self.get("Studiengang Wahl 2"))
                if s and s.strip().upper() not in IGNORED_STUDIENGAENGE]


def fetch_feed(url: str) -> list[Entry]:
    req = urllib.request.Request(url, headers={"User-Agent": "schnuppertag-notify/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = resp.read()
    root = ET.fromstring(data)
    entries = []
    for item in root.iter("item"):
        link = (item.findtext("link") or "").strip()
        m = re.search(r"rid=(\d+)", link)
        entries.append(Entry(
            rid=m.group(1) if m else link,
            school=(item.findtext("title") or "").strip(),
            link=link,
            date=(item.findtext("pubDate") or "").strip(),
            fields=parse_description(item.findtext("description") or ""),
        ))
    return entries


# ------------------------------------------------------------- betreuer ----
@dataclass
class Betreuer:
    studiengang: str
    name: str
    mail: str


def read_text_any(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "cp1252"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1")


def load_betreuer(path: Path) -> list[Betreuer]:
    rows = csv.DictReader(read_text_any(path).splitlines(), delimiter=";")
    out = []
    for r in rows:
        mail = (r.get("Mail") or "").strip()
        sg = (r.get("Studiengang") or "").strip()
        if not mail or not sg:
            continue
        name = f"{(r.get('Vorname') or '').strip()} {(r.get('Nachname') or '').strip()}".strip()
        out.append(Betreuer(sg, name, mail))
    return out


class Aliases:
    """Alias-Gruppen: alle Kürzel einer Gruppe gelten als gleichbedeutend."""

    def __init__(self, groups: list[list[str]]):
        self.map: dict[str, str] = {}
        for g in groups:
            if not g:
                continue
            canon = g[0].strip().upper()
            for code in g:
                self.map[code.strip().upper()] = canon

    def canon(self, code: str) -> str:
        c = code.strip().upper()
        return self.map.get(c, c)


# ----------------------------------------------------------------- mails ----
def assign(entries: list[Entry], betreuer: list[Betreuer], aliases: Aliases):
    by_sg: dict[str, list[Betreuer]] = {}
    for b in betreuer:
        by_sg.setdefault(aliases.canon(b.studiengang), []).append(b)

    per_mail: dict[str, tuple[Betreuer, dict[str, Entry]]] = {}
    unassigned: list[Entry] = []
    for e in entries:
        hit = False
        for sg in e.studiengaenge:
            for b in by_sg.get(aliases.canon(sg), []):
                key = b.mail.lower()
                per_mail.setdefault(key, (b, {}))[1][e.rid] = e
                hit = True
        if not hit:
            unassigned.append(e)
    return per_mail, unassigned


FIELDS = [
    ("Schule", lambda e: e.school),
    ("Name", lambda e: e.name),
    ("E-Mail", lambda e: e.get("E-Mail")),
    ("Telefon", lambda e: e.get("Telefon")),
    ("Wahl 1", lambda e: e.get("Studiengang Wahl 1")),
    ("Wahl 2", lambda e: e.get("Studiengang Wahl 2")),
    ("Termin", lambda e: e.get("Termin")),
    ("Anmerkung", lambda e: e.get("Anmerkung")),
]


def build_message(cfg: dict, b: Betreuer, entries: list[Entry], to_override: str | None) -> EmailMessage:
    smtp = cfg["smtp"]
    count = len(entries)
    first = b.name.split(" ")[0] if b.name else ""

    lines = [f"Hallo {first},".strip(), "",
             f"es gibt {count} offene Schnuppertag-Anmeldung(en) mit Status "
             f"\"{cfg['status_trigger']}\" für deinen Studiengang. Bitte bearbeiten:", ""]
    for e in entries:
        for label, fn in FIELDS:
            val = fn(e)
            if val:
                lines.append(f"  {label}: {val}".replace("\n", "\n    "))
        lines.append(f"  Link: {e.link}")
        lines.append("")
    lines.append("Diese Erinnerung wird automatisch gesendet, solange der Status unverändert ist.")
    text = "\n".join(lines)

    esc = html.escape
    rows = []
    for e in entries:
        cells = "".join(
            f"<td style='padding:4px 8px;border:1px solid #ccc;vertical-align:top'>"
            f"{esc(fn(e)).replace(chr(10), '<br>')}</td>" for _, fn in FIELDS)
        cells += (f"<td style='padding:4px 8px;border:1px solid #ccc'>"
                  f"<a href='{esc(e.link)}'>öffnen</a></td>")
        rows.append(f"<tr>{cells}</tr>")
    head = "".join(f"<th style='padding:4px 8px;border:1px solid #ccc;background:#eee'>{esc(l)}</th>"
                   for l, _ in FIELDS) + "<th style='padding:4px 8px;border:1px solid #ccc;background:#eee'>Link</th>"
    html_body = (
        f"<p>Hallo {esc(first)},</p>"
        f"<p>es gibt <b>{count}</b> offene Schnuppertag-Anmeldung(en) mit Status "
        f"<b>{esc(cfg['status_trigger'])}</b> für deinen Studiengang. Bitte bearbeiten:</p>"
        f"<table style='border-collapse:collapse;font-family:sans-serif;font-size:13px'>"
        f"<tr>{head}</tr>{''.join(rows)}</table>"
        f"<p style='color:#666;font-size:12px'>Diese Erinnerung wird automatisch gesendet, "
        f"solange der Status unverändert ist.</p>"
    )

    msg = EmailMessage()
    msg["Subject"] = smtp.get("subject", "Offene Schnuppertag-Anmeldungen ({count})").format(count=count)
    msg["From"] = smtp["from"]
    msg["To"] = to_override or b.mail
    if smtp.get("reply_to"):
        msg["Reply-To"] = smtp["reply_to"]
    msg.set_content(text)
    msg.add_alternative(html_body, subtype="html")
    return msg


def send_all(cfg: dict, messages: list[EmailMessage]) -> None:
    smtp = cfg["smtp"]
    user = os.environ.get("SMTP_USER", "")
    pw = os.environ.get("SMTP_PASSWORD", "")
    port = int(smtp.get("port", 587))
    if port == 465:
        conn = smtplib.SMTP_SSL(smtp["host"], port, timeout=60)
    else:
        conn = smtplib.SMTP(smtp["host"], port, timeout=60)
        conn.ehlo()
        if smtp.get("starttls", True):
            conn.starttls()
            conn.ehlo()
    with conn:
        if user:
            conn.login(user, pw)
        for m in messages:
            conn.send_message(m)
            log.info("gesendet an %s (%s)", m["To"], m["Subject"])


# ------------------------------------------------------------------ main ----
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=str(BASE / "config.toml"))
    ap.add_argument("--dry-run", action="store_true", help="nichts senden, nur ausgeben")
    ap.add_argument("--to-me", metavar="ADDR", help="alle Mails an diese Adresse umleiten")
    ap.add_argument("--scheduled", action="store_true",
                    help="vom Timer aufgerufen: überspringen, wenn letzter Versand < schedule.every_days her")
    args = ap.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(BASE / "notify.log", encoding="utf-8"),
                  logging.StreamHandler(sys.stdout)],
    )
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    cfg = load_config(Path(args.config))
    trigger = cfg.get("status_trigger", "_Neu").strip()

    state_file = BASE / ".last_run"
    if args.scheduled:
        every = int(cfg.get("schedule", {}).get("every_days", 1))
        try:
            last = date.fromisoformat(state_file.read_text().strip())
        except (OSError, ValueError):
            last = None
        if last and (date.today() - last).days < every:
            log.info("Übersprungen: letzter Lauf %s, Intervall %d Tag(e).", last, every)
            return 0

    entries = fetch_feed(cfg["feed_url"])
    open_entries = [e for e in entries if e.get("Status").strip() == trigger]
    log.info("Feed: %d Einträge, %d mit Status %r", len(entries), len(open_entries), trigger)

    betreuer = load_betreuer(BASE / cfg.get("betreuer_csv", "betreuer.csv"))
    aliases = Aliases(cfg.get("aliases", {}).get("groups", []))
    per_mail, unassigned = assign(open_entries, betreuer, aliases)

    for e in unassigned:
        log.warning("Kein:e Betreuer:in für %s (%s) – Studiengang %s",
                    e.name, e.school, ", ".join(e.studiengaenge) or "?")

    messages = [build_message(cfg, b, list(ents.values()), args.to_me)
                for b, ents in per_mail.values()]

    fallback = cfg.get("fallback_to")
    if unassigned and fallback:
        fb = Betreuer("", "", fallback)
        messages.append(build_message(cfg, fb, unassigned, args.to_me))

    if not messages:
        log.info("Nichts zu senden.")
        return 0

    if args.dry_run:
        for (b, ents) in per_mail.values():
            log.info("[dry-run] %s <%s>: %s", b.name, b.mail,
                     "; ".join(f"{e.name} ({'/'.join(e.studiengaenge)})" for e in ents.values()))
        if unassigned and fallback:
            log.info("[dry-run] Fallback <%s>: %d Einträge", fallback, len(unassigned))
        print("\n----- Beispielmail -----\n" + messages[0].get_body(("plain",)).get_content())
        return 0

    send_all(cfg, messages)
    log.info("%d Mail(s) gesendet.", len(messages))
    if args.scheduled:
        state_file.write_text(date.today().isoformat())
    return 0


if __name__ == "__main__":
    sys.exit(main())
