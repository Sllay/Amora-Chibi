FROM node:22.23.4-alpine3.24

ENV NODE_ENV=production
WORKDIR /opt/amora

# libvips provides bounded native artwork resizing without bringing an npm
# image-decoder dependency into the long-running Discord worker.
RUN apk add --no-cache vips-tools ffmpeg && \
    command -v vipsthumbnail && command -v ffmpeg && \
    addgroup -g 10001 amora && adduser -D -u 10001 -G amora amora

COPY --chown=10001:10001 workers/common ./workers/common
COPY --chown=10001:10001 workers/discord ./workers/discord

USER 10001:10001

CMD ["node", "workers/discord/entrypoint.mjs"]
