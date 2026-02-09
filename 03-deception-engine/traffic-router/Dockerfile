FROM openresty/openresty:alpine

RUN if [ -x /usr/local/openresty/bin/opm ]; then \
      /usr/local/openresty/bin/opm get openresty/lua-resty-redis || true; \
    else \
      echo "opm not found in image; continuing"; \
    fi \
    && mkdir -p /tmp/nginx

COPY nginx.conf /usr/local/openresty/nginx/conf/nginx.conf
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 80

ENTRYPOINT ["/entrypoint.sh"]
