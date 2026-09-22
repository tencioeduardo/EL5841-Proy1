#!/usr/bin/env bash
# Abre el contenedor de compilación. Correr en el HOST.
# Todo ~/yocto queda montado en /workdir dentro del contenedor (misma carpeta, dos vistas).
# Si da "permission denied" con docker: sudo usermod -aG docker $USER  y cierra sesión.
cd "$(dirname "$0")/.."
exec docker run --rm -it -v "$PWD":/workdir crops/poky:ubuntu-22.04 --workdir=/workdir
