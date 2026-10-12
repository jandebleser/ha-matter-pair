ARG BUILD_FROM
FROM $BUILD_FROM

RUN apk add --no-cache python3 py3-aiohttp

COPY backend /app
COPY run.sh /run.sh
RUN chmod +x /run.sh

CMD ["/run.sh"]
