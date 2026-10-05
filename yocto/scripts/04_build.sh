#!/usr/bin/env bash
# Compila. Correr DENTRO del contenedor.
# Uso: ./04_build.sh [modo] [imagen]
#   verificar  → prueba en seco: detecta nombres de paquetes/recetas malos en minutos
#   descargar  → solo descarga fuentes (útil si luego no tendrás buena red)
#   build      → compila (por defecto)
MODO=${1:-build}; IMG=${2:-core-image-minimal}
cd /workdir || exit 1
source poky/oe-init-build-env build >/dev/null || exit 1

case "$MODO" in
  verificar) bitbake -n "$IMG" && echo "== $IMG: dependencias resueltas OK ==" ;;
  descargar) bitbake --runall=fetch "$IMG" ;;
  build)
    time bitbake -k "$IMG"
    echo; ls -lh tmp/deploy/images/*/"$IMG"-*.wic.* 2>/dev/null
    ls tmp/deploy/images/*/"$IMG"-*.manifest 2>/dev/null ;;
  *) echo "Modo desconocido: $MODO"; exit 1 ;;
esac
