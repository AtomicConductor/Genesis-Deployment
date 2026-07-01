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

Remaining (in order):
1. [ ] **Mirror the 4 images into `zot-dev`** (`crane copy`, per
   `k8s-services-infra/docs/IMAGE_BUILD_PIPELINE.md` s5) -- one-off in-cluster Job as
   `robot-pusher`, with a temporary Docker Hub egress allowance on the `image-builds`
   CNP (revert after). Needs the `robot-pusher` credential.
2. [ ] **Seal `zot-pull`** (`robot-puller`, read-only) `--scope strict` for
   `genesis-dev` and apply it (onboarding doc step 6). Needs the `robot-puller`
   credential.
3. [ ] **Repoint the app-of-apps** `genesis` entry (in `k8s-services-internal`
   `argocd-apps/values-dev-us-east-04a.yaml`) from `juno-fx/coreweave` to
   `AtomicConductor/Genesis-Deployment` @ `lean-vdi`. Do this AFTER 1 and 2 so the
   sync does not `ImagePullBackOff`.
4. [ ] **RBAC**: the chart still grants Genesis a `*/*/*` cluster-admin ClusterRole.
   Scope it to what Titan uses (the `juno-innovations.com` CRDs + the namespaces it
   schedules VDIs into + pods/services/secrets) or get platform sign-off.
5. [ ] **Confirm the VDI session path**: verify a running workstation is reached
   through the Genesis hostname (web/streamed) and does not open a separate per-session
   port that would need its own WARP route.

## Repoint diff (step 3)

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
