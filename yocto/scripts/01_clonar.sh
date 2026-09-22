#!/usr/bin/env bash
# Descarga las capas (rama scarthgap). Correr en el HOST, dentro de ~/yocto.
set -e
cd "$(dirname "$0")/.."
RAMA=scarthgap
clonar(){ # $1 url  $2 carpeta
  if [ -d "$2/.git" ]; then echo "Actualizando $2"; git -C "$2" pull --ff-only
  else git clone -b $RAMA "$1" "$2"; fi
}
clonar https://git.yoctoproject.org/poky                        poky
clonar https://github.com/agherzan/meta-raspberrypi             meta-raspberrypi
clonar https://git.openembedded.org/meta-openembedded           meta-openembedded
mkdir -p downloads sstate-cache

# Registro de versiones exactas (para la bitácora y para que tu compañero use lo mismo)
{ date; for r in poky meta-raspberrypi meta-openembedded; do
    echo "$r $(git -C $r rev-parse --short HEAD) $(git -C $r log -1 --format=%cd --date=short)"; done
} | tee versiones_capas.txt
