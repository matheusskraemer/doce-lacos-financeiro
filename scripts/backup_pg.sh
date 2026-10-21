#!/usr/bin/env bash
# Backup completo do PostgreSQL com pg_dump (formato custom), criptografado com GPG.
# Uso: DATABASE_URL=postgresql://... BACKUP_PASSPHRASE=... ./scripts/backup_pg.sh [pasta_destino]
set -euo pipefail
: "${DATABASE_URL:?Defina DATABASE_URL}"
: "${BACKUP_PASSPHRASE:?Defina BACKUP_PASSPHRASE (guarde-a no seu cofre de senhas)}"
DEST="${1:-./backups}"
mkdir -p "$DEST"
STAMP="$(date +%Y%m%d-%H%M%S)"
FILE="$DEST/docelacos_$STAMP.dump"
pg_dump --format=custom --no-owner --no-privileges "$DATABASE_URL" --file "$FILE"
pg_restore --list "$FILE" > /dev/null   # verifica se o arquivo é legível
gpg --batch --yes --pinentry-mode loopback --passphrase "$BACKUP_PASSPHRASE" --symmetric --cipher-algo AES256 \
    --output "$FILE.gpg" "$FILE"
shred -u "$FILE" 2>/dev/null || rm -f "$FILE"
echo "Backup criado: $FILE.gpg"
