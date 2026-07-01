# Orion / Genesis -- Conductor lean-VDI integration

How we run **only** Orion's Linux-VDI workstation engine as a tenant on the existing
Conductor CKS cluster (`dev-us-east-04a`), WARP-private, with **no** second Argo CD,
**no** k3s, and **no** duplicated infrastructure. This branch (`lean-vdi`) is the
trimmed chart.

> Scope: the integration, not Orion internals. Upstream docs:
> <https://juno-fx.github.io/Orion-Documentation/>.

---

## TL;DR

- Deploy the trimmed chart as one app-of-apps tenant in `genesis-dev`, synced by the
  existing `argocd-internal`. No `argocd-orion`.
- Expose WARP-only at `https://juno.dev.conductor.technology` via the shared
  `internal-cloudflared` connector + `warp-route-reconciler`, with the in-pod nginx
  TLS sidecar on `:443`.
- Titan schedules VDI pods onto the cluster's existing L40 GPU nodes via the plain
  Kubernetes API. Argo CD is never in the workstation path.
- Images come from the internal **Zot** registry (no Docker Hub egress on CKS nodes).

## Why no second Argo CD

Only **Terra** (the marketplace) talks to Argo CD (`argoproj.io/*`). We deleted Terra,
so nothing in the lean chart needs Argo CD beyond the one that deploys it. The VDI
flow is: Genesis UI -> creates a `Workstation` CR -> Titan reconciles -> Titan spawns
the GPU pod natively. Rhea (auth) is a per-pod sidecar in Genesis and Titan, not a
Terra-only component, so dropping Terra does not remove auth.

## Cluster reality (ground truth, `dev-us-east-04a`)

- GPUs: 2 nodes x 8 = **16 NVIDIA L40** (label `nvidia.com/gpu.product=NVIDIA-L40`).
  NOTE: labeled L40, not L40S -- confirm that's the intended hardware.
- Already present, reused (not duplicated): `argocd-internal`, `internal-cloudflared`,
  `zot` / `zot-dev` (warp SVC `10.16.0.146:443`, backend `:5000`), `genesis-dev`.
- In `genesis-dev` already: the `genesis-tls` cert (cert-manager), `juno-auth-secret`
  (NextAuth `AUTH_SECRET`), the `cks-netpol=cilium` label, and a GitOps-managed
  `genesis` CiliumNetworkPolicy (`ingress: fromEntities: cluster`, `egress: all`).

## The chart (this branch)

Based on the fork's **`dev`** branch -- which implements WARP + the TLS proxy sidecar
and gates the nginx Ingress on `not warp.enabled`. (The previously deployed
`juno-fx/coreweave` branch does NOT: its ingress is ungated and it has no TLS sidecar,
which is why the live app was `Degraded`, "waiting for healthy state of
Ingress/genesis-ingress", with `genesis-dev` empty.)

Kept (VDI path): `templates/crds/*` (Workstation/User/Group), `templates/titan/*`,
`templates/genesis/*` (deployment/service/SA/role/binding + nextauth Job +
token-bootstrap + `genesis-proxy` TLS ConfigMap + `genesis-certificate`),
`templates/rhea/*` policy ConfigMaps + `files/rhea/*.cedar`.

Deleted (not VDI): `templates/terra/*`, `templates/mertrics-gatherer/*`,
`templates/genesis/juno-playbook-k3s-provision.cm.yaml` +
`files/genesis/juno-playbook-k3s-provision.yml`, `files/dashboards/*`, `juno/kind.yaml`.

Images (all four repointed at Zot in `values-dev.yaml`):

| Component | Source (Docker Hub) | Zot target |
|---|---|---|
| genesis | `junoinnovations/genesis:v6.0.0` | `zot.dev.conductor.technology/junoinnovations/genesis:v6.0.0` |
| titan | `junoinnovations/titan:v2.1.2` | `.../junoinnovations/titan:v2.1.2` |
| rhea | `junoinnovations/rhea:v1.2.3` | `.../junoinnovations/rhea:v1.2.3` |
| proxy | `nginx:1.27-alpine` | `zot.dev.conductor.technology/library/nginx:1.27-alpine` |

`values-dev.yaml` sets `image_pull_secret: zot-pull`, `warp.enabled: true`,
`tls.enabled: true`, and the NextAuth env.

## Status

Done:
- [x] Chart trimmed to the lean VDI set; validated (`helm lint` clean, render has no
      terra/metrics/k3s/ingress, TLS sidecar on `:443`). Pushed as `lean-vdi`.
- [x] Namespace baseline: `cks-netpol=cilium` label + `genesis` CNP already applied.
- [x] `genesis-tls` cert + `juno-auth-secret` already present.

- [x] **Images mirrored into `zot-dev`** (see runbook below).
- [x] **`zot-pull` secret** created in `genesis-dev` (see runbook below).
- [x] **App-of-apps repointed** -- `k8s-services-internal` PR #77 (`genesis-lean-vdi`
      -> `dev`), held for review. Merging it triggers the Argo sync.

Open (require a decision / a running app):
- [ ] **RBAC**: the chart still grants Genesis a `*/*/*` cluster-admin ClusterRole
      (`templates/genesis/genesis.role.yaml`). Scope it to what Genesis/Titan actually
      use (the `juno-innovations.com` CRDs + the namespaces Titan schedules VDIs into +
      pods/services/secrets) or get platform sign-off. Best done after watching it run.
- [ ] **Confirm the VDI session path**: after merge, create a Workstation and verify the
      session reaches users through the Genesis hostname (web/streamed) and does not open
      a separate per-session port that would need its own WARP route.

## Runbook: how the images were mirrored + `zot-pull` created

These were run from a workstation with WARP connectivity and `kubectl` pointed at
`dev-us-east-04a`. The approach deliberately avoids an in-cluster build Job and a
temporary Docker Hub egress change on the shared `image-builds` CNP (which Argo would
self-heal): the four images are **public**, so we pull them over the workstation's own
internet and push straight to the Zot **backend** `:5000` (where basic-auth works --
the WARP `:443` front returns 401 for basic-auth) via a port-forward. No shared infra
is mutated. Credentials are handled base64/opaque and never printed or committed.

```bash
# 0. crane (github.com/google/go-containerregistry). Any recent release works.

# 1. Port-forward the Zot backend (basic-auth-capable) to localhost.
kubectl -n zot-dev port-forward svc/zot 5000:5000    # leave running

# 2. robot-pusher credential (write). Recover from an existing zot-push secret;
#    do NOT echo the value. Login writes to a THROWAWAY docker config we delete after.
export DOCKER_CONFIG="$(mktemp -d)"
PUSHER_PW="$(kubectl -n image-builds-dev get secret zot-push \
  -o jsonpath='{.data.\.dockerconfigjson}' | base64 -d \
  | jq -r '.auths."zot.dev.conductor.technology".password')"
crane auth login localhost:5000 -u robot-pusher -p "$PUSHER_PW" --insecure

# 3. Copy the four images Docker Hub -> Zot backend (--insecure: backend cert has no
#    localhost SAN). Repo path is preserved, so runtime pulls of
#    zot.dev.conductor.technology/<path> resolve to the same blobs.
crane copy docker.io/junoinnovations/genesis:v6.0.0 localhost:5000/junoinnovations/genesis:v6.0.0 --insecure
crane copy docker.io/junoinnovations/titan:v2.1.2   localhost:5000/junoinnovations/titan:v2.1.2   --insecure
crane copy docker.io/junoinnovations/rhea:v1.2.3    localhost:5000/junoinnovations/rhea:v1.2.3    --insecure
crane copy docker.io/library/nginx:1.27-alpine      localhost:5000/library/nginx:1.27-alpine      --insecure

# 4. Verify, then wipe the throwaway credential config.
for r in junoinnovations/genesis junoinnovations/titan junoinnovations/rhea library/nginx; do
  crane ls "localhost:5000/$r" --insecure
done
rm -rf "$DOCKER_CONFIG"; unset PUSHER_PW DOCKER_CONFIG
```

`zot-pull` (robot-puller, read-only) is the **same** dockerconfig every other app uses
for the same Zot host, so we copy it verbatim rather than re-seal -- no plaintext ever
touches the shell:

```bash
kubectl -n internal-status-dev get secret zot-pull -o jsonpath='{.data.\.dockerconfigjson}' \
  | xargs -I{} kubectl create secret generic zot-pull -n genesis-dev \
      --type=kubernetes.io/dockerconfigjson --from-literal=.dockerconfigjson='{}' \
      --dry-run=client -o yaml | kubectl apply -f -
# verified against the backend: robot-puller -> GET /v2/ = 200, POST upload = 403 (read-only)
```

> Note on tooling: on the Windows workstation this was actually run with PowerShell
> equivalents (the dockerconfig is parsed with `ConvertFrom-Json`, the secret is applied
> from an inline manifest via `kubectl apply -f -`), but the mechanics are identical to
> the bash above.

## Repoint diff (PR #77)

```yaml
genesis:
  # was: repoURL: https://github.com/juno-fx/Genesis-Deployment.git
  #      targetRevision: coreweave
  repoURL: https://github.com/AtomicConductor/Genesis-Deployment.git
  targetRevision: lean-vdi
  path: "."
  helm:
    releaseName: genesis
    valueFiles:
      - values-dev.yaml
```

## For prod later

The prod twin uses the same chart with a prod-shaped overlay: point the image registries
at `zot.conductor.technology` (mirror the same four images there), issue the cert for the
prod hostname, and add a `genesis-<ns>` entry to the prod cilium policies. Nothing about
the mirror/`zot-pull` procedure changes except the Zot host.
