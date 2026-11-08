FROM oven/bun:1.4.3-slim AS bun

FROM python:3.12-slim
# mar serve shells out to `omp usage --json` for every quota cell except
# OpenRouter's direct API fetch, so the CLI must live in the server image.
COPY --from=bun /usr/local/bin/bun /usr/local/bin/bun
ENV BUN_INSTALL=/usr/local
RUN bun add -g @oh-my-pi/pi-coding-agent@18.11.3 \
    && command -v omp
WORKDIR /srv/mar
COPY . /srv/mar
RUN pip install --no-cache-dir .
ENV MAR_HOME=/data PYTHONUNBUFFERED=1
VOLUME ["/data"]
EXPOSE 8930
ENTRYPOINT ["mar", "serve", "--host", "0.0.0.0", "--port", "8930"]
