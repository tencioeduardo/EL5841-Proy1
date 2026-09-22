#!/usr/bin/env bash
# Graba la imagen en la microSD. Correr en el HOST.
# Uso: ./05_flashear.sh /dev/sdX|/dev/mmcblkN [imagen]
# Alternativa gráfica: balenaEtcher acepta el .wic.bz2 directamente.
set -e
cd "$(dirname "$0")/.."
DEV=${1:-}; IMG=${2:-core-image-minimal}
if [ -z "$DEV" ]; then echo "Uso: $0 /dev/DISPOSITIVO [imagen]"; lsblk -d -o NAME,SIZE,RM,TRAN,MODEL; exit 1; fi
[ -b "$DEV" ] || { echo "!! $DEV no es un dispositivo de bloque"; exit 1; }

F=$(ls build/tmp/deploy/images/raspberrypi4-64/"$IMG"-raspberrypi4-64.rootfs.wic.bz2)

# Protección: nunca el disco donde está montado el sistema
RAIZ=/dev/$(lsblk -no PKNAME "$(findmnt -no SOURCE /)")
[ "$DEV" = "$RAIZ" ] && { echo "!! $DEV es el disco del sistema. Abortando."; exit 1; }
GB=$(( $(lsblk -bdno SIZE "$DEV") / 1024 / 1024 / 1024 ))
[ $GB -gt 256 ] && { echo "!! $DEV mide ${GB} GB: no parece una microSD. Abortando."; exit 1; }

lsblk -o NAME,SIZE,MODEL,MOUNTPOINT "$DEV"
read -rp "Se BORRARÁ $DEV (${GB} GB). Escribe el dispositivo para confirmar: " C
[ "$C" = "$DEV" ] || { echo "Cancelado"; exit 1; }
for p in $(lsblk -lno NAME "$DEV" | tail -n +2); do sudo umount "/dev/$p" 2>/dev/null || true; done

if command -v bmaptool >/dev/null && [ -f "${F%.bz2}.bmap" ]; then
  sudo bmaptool copy "$F" "$DEV"          # solo escribe bloques usados (sudo apt install bmap-tools)
else
  bzcat "$F" | sudo dd of="$DEV" bs=4M status=progress conv=fsync
fi
sync; echo "Listo. Saca la microSD."
