#!/bin/sh
# De Custom App geeft de broker per start eigen hashes en een ACL. Bied die aan
# onder de paden van het Mosquitto-profiel en start dezelfde pakketbinary.
set -eu
[ "$#" -eq 2 ] && [ "$1" = -c ] || exec /usr/sbin/mosquitto "$@"
bron=$(dirname "$2")
doel=/etc/mosquitto/conf.d
test /appdata/mosquitto -ef /var/lib/mosquitto
umask 077
install -m 0600 "$bron/mqtt-users" "$bron/mqtt-acl" "$doel/"
sed -e "s|^password_file .*/|password_file $doel/|" \
    -e "s|^acl_file .*/|acl_file $doel/|" \
    -e 's|^persistence_location /appdata/mosquitto/$|persistence_location /var/lib/mosquitto/|' \
    "$2" > "$doel/mosquitto.conf"
exec /usr/sbin/mosquitto -c "$doel/mosquitto.conf"
