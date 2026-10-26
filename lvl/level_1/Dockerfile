FROM nginxinc/nginx-unprivileged:alpine

USER root

ENV LOG_DIR=/app/logs

RUN mkdir -p \
      /app/logs \
      /tmp/client_temp \
      /tmp/proxy_temp \
      /tmp/fastcgi_temp \
      /tmp/uwsgi_temp \
      /tmp/scgi_temp \
    && chown -R nginx:nginx /app/logs \
      /tmp/client_temp \
      /tmp/proxy_temp \
      /tmp/fastcgi_temp \
      /tmp/uwsgi_temp \
      /tmp/scgi_temp

COPY nginx.conf /etc/nginx/nginx.conf
COPY --chown=nginx:nginx html/ /usr/share/nginx/html/
COPY --chown=nginx:nginx common/solve.js /usr/share/nginx/html/__solve.js

USER nginx

EXPOSE 5000

CMD ["nginx", "-g", "daemon off;"]