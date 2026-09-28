#!/usr/bin/env bash
# salud.sh PID [INTERVALO_S] -- muestrea RSS, fds e hilos de un proceso a salud.csv
PID="$1"; INT="${2:-60}"
echo "t_s,rss_kb,fd,hilos" > salud.csv
T0=$(date +%s)
while kill -0 "$PID" 2>/dev/null; do
  echo "$(( $(date +%s) - T0 )),$(awk '/VmRSS/{print $2}' /proc/$PID/status),$(ls /proc/$PID/fd | wc -l),$(awk '/Threads/{print $2}' /proc/$PID/status)" >> salud.csv
  sleep "$INT"
done
