#!/usr/bin/env bash
# Restaura um backup criado por backup_pg.sh em um banco VAZIO (ou de teste).
# Uso: TARGET_DATABASE_URL=postgresql://... BACKUP_PASSPHRASE=... ./scripts/restore_pg.sh arquivo.dump.gpg
set -euo pipefail
: "${TARGET_DATABASE_URL:?Defina TARGET_DATABASE_URL (banco de destino)}"
: "${BACKUP_PASSPHRASE:?Defina BACKUP_PASSPHRASE}"
IN="${1:?Informe o arquivo .dump.gpg}"
TMP="$(mktemp --suffix=.dump)"
trap 'shred -u "$TMP" 2>/dev/null || rm -f "$TMP"' EXIT
gpg --batch --yes --pinentry-mode loopback --passphrase "$BACKUP_PASSPHRASE" --decrypt --output "$TMP" "$IN"
pg_restore --no-owner --no-privileges --clean --if-exists --dbname "$TARGET_DATABASE_URL" "$TMP"
echo "Restauração concluída. Rode: DATABASE_URL=\$TARGET_DATABASE_URL flask --app wsgi check"
