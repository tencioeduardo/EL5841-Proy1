# Calcula paralelismo. Se usa con: source hilos.sh
# BB_NUMBER_THREADS = recetas en paralelo; PARALLEL_MAKE = procesos de compilación por receta.
# El peor caso es BB × PM procesos, pero rara vez coinciden muchas recetas pesadas a la vez.
N=$(nproc); RAM=$(awk '/MemTotal/{printf "%d", $2/1024/1024}' /proc/meminfo)
BB=$(( N / 2 ));     [ $BB -gt $(( RAM / 2 )) ] && BB=$(( RAM / 2 ))
PM=$(( RAM / 2 + 1 )); [ $PM -gt $N ] && PM=$N
[ $BB -lt 1 ] && BB=1; [ $PM -lt 1 ] && PM=1
