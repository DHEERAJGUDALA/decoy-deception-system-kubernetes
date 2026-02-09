#!/bin/sh
set -e

DNS=$(grep -m1 '^nameserver' /etc/resolv.conf | awk '{print $2}')
DNS="${DNS:-10.43.0.10}"

sed -i "s/__RESOLVER__/${DNS}/g" /usr/local/openresty/nginx/conf/nginx.conf

echo "traffic-router starting — resolver=${DNS}"
exec /usr/local/openresty/bin/openresty -g 'daemon off;'
