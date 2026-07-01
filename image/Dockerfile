# Genesis lean-VDI image: upstream Next.js frontend + un-frozen, namespace-
# parametrized Python backend.
#
# Why this exists: upstream junoinnovations/genesis ships the backend as a
# PyInstaller-frozen binary (/src.app) that HARDCODES the "argocd" namespace for
# its platform resources (schema-cache configmaps, workload-template CRs). There
# is no public Genesis source and the value is not env-configurable, so we extract
# the frozen bundle, run it un-frozen under stock CPython 3.12, and inject a shim
# (ns_patch.py) that redirects the hardcoded namespace to this deployment's
# namespace at the kubernetes-client layer. The frontend is copied verbatim.
#
# Base images are both Alpine 3.24.1 (identical musl), so the extracted musl
# CPython-3.12 extension .so files and the copied Node binary are ABI-compatible.

FROM junoinnovations/genesis:v6.0.0 AS upstream

FROM python:3.12-alpine

# Node runtime + the unchanged standalone Next.js frontend from upstream.
COPY --from=upstream /usr/local/bin/node /usr/local/bin/node
COPY --from=upstream /prod /prod
# Node needs libstdc++/libgcc at runtime.
RUN apk add --no-cache libstdc++

# Un-frozen backend bundle (all deps as musl .pyc/.so) + our parametrization shim.
COPY app /app
COPY ns_patch.py run_backend.py /app/
# The backend reads data files by absolute build-time path under /app/src/backend
# (e.g. header/juno-ascii.txt) and serves /app/src/static (swagger UI). Upstream
# ships the whole source tree there on the filesystem next to the frozen binary.
# We reproduce it verbatim for those data files. The .py under /app/src are NOT on
# sys.path (run_backend uses /app), so imports still resolve to our patched bundle.
COPY --from=upstream /app/src /app/src
COPY launch-prod.sh /prod/launch-prod.sh

ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    AUTH_TRUST_HOST=true \
    PORT=3000 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /prod
ENTRYPOINT ["sh", "/prod/launch-prod.sh"]
