#!/bin/sh
# Alleen openbare proefbestanden uit de tijdelijke diagnose-image.
id
uname -smr
cat /proc/self/attr/current 2>/dev/null || true
printf 'onbekende_proefoptie\n' > /tmp/proef.conf
for bestand in /mosquitto/config/mosquitto.conf /etc/mosquitto/mosquitto.conf /etc/mosquitto/conf.d/proef.conf /appdata/proef.conf /tmp/proef.conf; do
  ls -ldn "$bestand"
  cat "$bestand" >/dev/null
  mosquitto -c "$bestand" 2>&1 || true
  strace -qq -s 160 -o /tmp/bestandsaanroepen -e trace=%file,%%stat mosquitto -c "$bestand" 2>&1 || true
  grep -E 'proef.conf|mosquitto.conf|EPERM|EACCES|EOVERFLOW|ENOSYS|EOPNOTSUPP|fstat\(' /tmp/bestandsaanroepen || true
done
mosquitto >/tmp/standaardopstart 2>&1 &
pid=$!
sleep 1
if kill -0 "$pid" 2>/dev/null; then
  cat "/proc/$pid/attr/current" 2>/dev/null || true
  grep -E '^(Uid|Gid|Seccomp|NoNewPrivs):' "/proc/$pid/status"
  kill "$pid"
else
  echo 'De broker zonder configuratiepad is gestopt.'
fi
wait "$pid" || true
