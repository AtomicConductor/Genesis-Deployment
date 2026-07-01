#!/bin/sh
# Genesis lean-VDI launcher. Runs the unchanged Next.js frontend (:3000) and the
# un-frozen, namespace-parametrized Python backend (:8000). Replaces the upstream
# launch that ran the PyInstaller-frozen /src.app for the backend.
echo "Starting Genesis (lean VDI, namespace-parametrized backend)"

touch /tmp/juno-frontend.log /tmp/juno-backend.log

# Match upstream: disable TLS verification for internal Kubernetes API calls.
export NODE_TLS_REJECT_UNAUTHORIZED=0

# Next.js standalone binds to $HOSTNAME; k8s sets that to the pod name (-> pod IP),
# which leaves nothing on loopback. The in-pod nginx TLS sidecar proxies to
# 127.0.0.1:3000, so force the frontend to bind all interfaces.
(cd /prod && HOSTNAME=0.0.0.0 node server.js) > /tmp/juno-frontend.log 2>&1 &
(cd /app && python3.12 run_backend.py) > /tmp/juno-backend.log 2>&1 &

tail -f /tmp/juno-frontend.log /tmp/juno-backend.log
