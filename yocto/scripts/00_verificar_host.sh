#!/usr/bin/env bash
# Verifica que la máquina puede compilar Yocto. Correr en el HOST.
cd "$(dirname "$0")/.."
ok(){ echo "  [OK]  $*"; }; mal(){ echo "  [!!]  $*"; }

echo "== SO =="
grep PRETTY_NAME /etc/os-release 2>/dev/null || uname -a
if grep -qi microsoft /proc/version 2>/dev/null; then
  echo "  WSL2 detectado"
  case "$PWD" in /mnt/*) mal "Estás en $PWD (disco de Windows): será MUY lento. Muévete a ~/yocto";; *) ok "Carpeta dentro de Linux";; esac
fi

echo "== CPU / RAM =="
source scripts/hilos.sh
echo "  núcleos: $N   RAM: ${RAM} GB"
echo "  paralelismo que se usará: BB_NUMBER_THREADS=$BB  PARALLEL_MAKE=-j $PM"
[ "$RAM" -lt 8 ] && mal "Menos de 8 GB: el build puede fallar por memoria (usa swap)" || ok "RAM suficiente"

echo "== Disco en $PWD =="
LIBRE=$(df -BG --output=avail . | tail -1 | tr -dc 0-9)
echo "  libre: ${LIBRE} GB"
if   [ "$LIBRE" -ge 100 ]; then ok "Suficiente para imagen completa"
elif [ "$LIBRE" -ge 50 ];  then mal "Alcanza para imagen mínima; la completa necesita ~100 GB (rm_work ayuda)"
else mal "Menos de 50 GB: no alcanza"; fi

echo "== Docker =="
if command -v docker >/dev/null; then
  docker --version
  docker info >/dev/null 2>&1 && ok "Docker funciona sin sudo" || mal "Docker no responde sin sudo: sudo usermod -aG docker $USER y cierra sesión"
else mal "Docker no instalado"; fi

echo "== Git =="
command -v git >/dev/null && ok "$(git --version)" || mal "git no instalado"
