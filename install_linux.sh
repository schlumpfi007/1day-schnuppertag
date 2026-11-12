#!/usr/bin/env bash
# Richtet einen systemd-Timer ein, der notify.py laut [schedule] in config.toml ausführt.
# Aufruf (im Projektordner):  sudo ./install_linux.sh
# Erneut ausführen, wenn sich [schedule] ändert.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN_USER="${SUDO_USER:-$(id -un)}"
PYTHON="$(command -v python3)"
NAME="schnuppertag-notify"

if [[ $EUID -ne 0 ]]; then
  echo "Bitte mit sudo ausführen." >&2
  exit 1
fi

"$PYTHON" -c 'import sys
try:
    import tomllib
except ModuleNotFoundError:
    import tomli
' 2>/dev/null || { echo "Python >= 3.11 oder 'pip install tomli' nötig." >&2; exit 1; }

TIME="$(sed -nE 's/^\s*time\s*=\s*"([0-9]{1,2}:[0-9]{2})".*/\1/p' "$DIR/config.toml" | head -1)"
TIME="${TIME:-08:00}"

[[ -f "$DIR/.env" ]] && chmod 600 "$DIR/.env" && chown "$RUN_USER" "$DIR/.env"

cat > "/etc/systemd/system/$NAME.service" <<EOF
[Unit]
Description=Schnuppertag-Erinnerungsmails
Wants=network-online.target
After=network-online.target

[Service]
Type=oneshot
User=$RUN_USER
WorkingDirectory=$DIR
ExecStart=$PYTHON $DIR/notify.py --scheduled
EOF

cat > "/etc/systemd/system/$NAME.timer" <<EOF
[Unit]
Description=Schnuppertag-Erinnerung täglich um $TIME prüfen

[Timer]
OnCalendar=*-*-* $TIME:00
Persistent=true

[Install]
WantedBy=timers.target
EOF

systemctl daemon-reload
systemctl enable --now "$NAME.timer"
echo "Timer aktiv: täglich $TIME (Intervall every_days wird vom Skript selbst geprüft)."
echo "Status:   systemctl list-timers $NAME.timer"
echo "Testlauf: sudo systemctl start $NAME.service && journalctl -u $NAME.service -n 50"
