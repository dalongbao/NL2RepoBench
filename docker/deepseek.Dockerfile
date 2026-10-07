FROM node:24-bookworm AS node-runtime
FROM python:3.12-bookworm

COPY --from=node-runtime /usr/local/ /usr/local/
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential git ripgrep ca-certificates \
    && rm -rf /var/lib/apt/lists/*

ENV CI=true ELECTRON_SKIP_BINARY_DOWNLOAD=1
WORKDIR /opt/deepseek-harness
COPY . .
# The build context is a git archive without .git, so supply the commit the harness build script would otherwise read.
ARG HARNESS_REVISION
RUN npm install --global pnpm@11.7.0 \
    && pnpm install --frozen-lockfile \
    && DSH_CLIENT_COMMIT_HASH="$HARNESS_REVISION" pnpm run build

LABEL org.nl2repobench.harness-revision=$HARNESS_REVISION
ENV DSH_HOME=/dsh-home DSH_PERMISSION_MODE=danger-full-access DSH_TELEMETRY_MODE=DISABLED
WORKDIR /workspace
ENTRYPOINT ["node", "/opt/deepseek-harness/apps/cli/lib/bin.js"]
