#!/bin/sh
# Expande la partición 3 (/data) hasta el final de la microSD.
# Corre una sola vez, ANTES de montar /data (la partición no está en uso).
set -e
DISCO=/dev/mmcblk0
PART=3
MARCA=/var/lib/expandir-datos.hecho

echo "expandir-datos: tamaño antes"; parted -s "$DISCO" unit MB print

parted -s "$DISCO" resizepart "$PART" 100%
e2fsck -f -y "${DISCO}p${PART}" || true   # resize2fs exige un fsck previo
resize2fs "${DISCO}p${PART}"

echo "expandir-datos: tamaño después"; parted -s "$DISCO" unit MB print
mkdir -p "$(dirname "$MARCA")" && touch "$MARCA"
