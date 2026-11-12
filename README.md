# Schnuppertag-Erinnerung

`notify.py` liest den Moodle-RSS-Feed der 1Day@FH-Anmeldungen und schickt jeder:m
Betreuer:in aus `betreuer.csv` eine Sammelmail mit allen offenen Anmeldungen
(`Status: _Neu`) des eigenen Studiengangs. Die Mail kommt bei jedem Lauf erneut,
solange der Status in Moodle unverändert bleibt.

**Ausführliche Anleitung (PDF):**
[Anleitung_schnuppertag-script.pdf](https://github.com/schlumpfi007/1day-schnuppertag/blob/main/anleitung/Anleitung_schnuppertag-script.pdf)
Sie behandelt Installation, RSS-Feed-Link, Konfiguration, Zeitplan, Aliase, Betreuer-Liste, Betrieb und Fehlerbehebung.

## Kurzfassung

Voraussetzung: Linux mit systemd, Python >= 3.11, SMTP-Postfach.

```bash
cd ~/schnuppertag
cp .env.example .env && nano .env          # SMTP_USER / SMTP_PASSWORD
nano config.toml                           # feed_url, smtp.from, schedule, aliases
python3 notify.py --dry-run                # Test ohne Versand
python3 notify.py --to-me deine@adresse    # Testmail an dich
sudo ./install_linux.sh                    # täglichen Timer aktivieren
```

| Datei              | Zweck                                         |
|--------------------|-----------------------------------------------|
| `notify.py`        | Skript (Feed lesen, zuordnen, Mails senden)   |
| `config.toml`      | Feed-URL, Status, Zeitplan, SMTP, Aliase      |
| `betreuer.csv`     | Betreuer:innen pro Studiengang (`;`-getrennt) |
| `.env`             | SMTP-Zugangsdaten (nie ins Git!)              |
| `install_linux.sh` | richtet den systemd-Timer ein                 |
