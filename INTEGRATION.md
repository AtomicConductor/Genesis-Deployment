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
| genesis | `junoinnovations/genesis:v6.0.0` (rebuilt, see below) | `zot.dev.conductor.technology/junoinnovations/genesis:v6.0.0-ns4` |
| titan | `junoinnovations/titan:v2.1.2` | `.../junoinnovations/titan:v2.1.2` |
| rhea | `junoinnovations/rhea:v1.2.3` | `.../junoinnovations/rhea:v1.2.3` |
| proxy | `nginx:1.27-alpine` | `zot.dev.conductor.technology/library/nginx:1.27-alpine` |

The **genesis** image is a local rebuild of upstream `v6.0.0` (`v6.0.0-ns4`) that
fixes a hardcoded namespace and makes the frontend work behind the TLS sidecar --
see "Runbook: namespace-parametrized genesis image" below. `v6.0.0` (unmodified)
is also mirrored in Zot.

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

- [x] **RBAC scoped to the release namespace, plus one narrow read-only ClusterRole.**
      Genesis's `*/*/*` cluster-admin `ClusterRole`/`ClusterRoleBinding`, the
      `genesis-token-bootstrap` cluster RBAC, and Titan's `ClusterRole`/`ClusterRoleBinding`
      are all now namespaced `Role`/`RoleBinding` bound in `{{ .Release.Namespace }}`
      (genesis-dev) -- full control, but only inside its own namespace. Orion's own CRDs are
      `scope: Cluster`, and Genesis/Titan genuinely need a few cluster-scoped **reads** at
      startup, so `templates/genesis/orion-crds.clusterrole.yaml` grants exactly:
      CRUD on the Orion CRDs (`juno-innovations.com` / `junovfx.com`
      groups/users/workstations/workstation-logs) + **read-only** (`get/list/watch`) on
      `services,pods,namespaces,endpoints,nodes,persistentvolumeclaims,persistentvolumes`,
      `apps/{deployments,replicasets,statefulsets}`, `storage.k8s.io/storageclasses`, and
      `apiextensions.k8s.io/customresourcedefinitions`. This is **not** cluster-admin: no
      cluster-wide writes and no cluster-wide secret access. Name is namespace-prefixed
      (`genesis-dev-orion-crds`) so multiple releases don't collide.
- [x] **Genesis backend deployed and healthy.** Startup completes
      (`Namespace: genesis-dev`, all schema/template/mount/storageclass caches refreshed,
      all watchers started, `Uvicorn running on 0.0.0.0:8000`), the frontend serves through
      the TLS sidecar (`GET / -> 308 /home`), Argo app `Synced`/`Healthy`. Runs in **community
      license mode** (2 workstations) until a real `juno-license` is supplied.

Open (require a running app):
- [ ] **Confirm the VDI session path**: create a Workstation and verify the session reaches
      users through the Genesis hostname (web/streamed) and does not open a separate
      per-session port that would need its own WARP route.
- [ ] **Real license** if more than 2 concurrent workstations are needed (currently community
      mode: `Failed to load the license: 402` -> `Shifting to community mode`).

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

## Runbook: namespace-parametrized genesis image (`v6.0.0-ns4`)

### Why upstream `v6.0.0` can't be used as-is

Upstream ships the Genesis **backend** as a PyInstaller-frozen onefile binary
(`/src.app`) with **no public source**. Two things break a non-`argocd` tenant:

1. **Hardcoded namespace.** `backend/workloads/service.py` (`Config.schemas`,
   `templates`, `_create/_update/_delete_workload_template`) passes a literal
   `namespace="argocd"` to the Kubernetes API for its schema-cache configmaps and
   workload-template CRs. Upstream's Makefile installs Genesis *into* a namespace
   named `argocd`, so the literal is never a problem for them. For us it caused a
   crash loop: `configmaps is forbidden ... in the namespace "argocd"`. It is **not**
   env-configurable (`GENESIS_NAMESPACE` only affects other components).
2. **Frontend binds to `$HOSTNAME`.** The Next.js standalone `server.js` binds to
   `process.env.HOSTNAME`, which Kubernetes sets to the pod name (-> pod IP). That
   leaves nothing on loopback, but our nginx TLS sidecar proxies to `127.0.0.1:3000`
   -> `502 Bad Gateway`. (Upstream fronts the pod with an Ingress to the pod IP, so
   they never hit this.)

### The fix (no decompilation, no bytecode surgery)

Run the backend **un-frozen** under stock CPython 3.12 (musl) and inject a tiny
runtime shim; copy the frontend verbatim and bind it to `0.0.0.0`.

- `ns_patch.py` -- wraps `ApiClient.call_api` on **both** the sync `kubernetes` and
  async `kubernetes_asyncio` clients. Any namespaced call whose `namespace == "argocd"`
  is redirected to the platform namespace, resolved from `$GENESIS_PLATFORM_NAMESPACE`
  -> `$GENESIS_NAMESPACE` -> the pod's service-account namespace -> `"argocd"`
  (unchanged fallback). Only the exact value `"argocd"` is rewritten, so
  per-user/project namespaces are untouched. This covers all five call sites at once.
- `run_backend.py` -- replacement entry point: puts the bundle on `sys.path`, imports
  `ns_patch` (applying it) **before** `from backend import app`, then runs the same
  `uvicorn.run(app, host="0.0.0.0", workers=1)` the frozen `__main__` did.
- `launch-prod.sh` -- runs the unchanged `node server.js` with `HOSTNAME=0.0.0.0`
  and our `python3.12 run_backend.py`.

Because the backend reads some data files by **absolute** build-time path
(`/app/src/static` swagger theme, `/app/src/backend/header/juno-ascii.txt`, ...),
the whole upstream `/app/src` tree is copied in for those assets. The `.py` there is
**not** on `sys.path` (imports resolve to the un-frozen bundle at `/app`).

`GENESIS_NAMESPACE` is already wired to `{{ .Release.Namespace }}` in
`genesis.deployment.yaml`, so the shim needs no extra config -- the same image works
in any namespace (dev/prod).

### Reproduce (Windows workstation; bash equivalents are obvious)

```powershell
# 0. tools: Docker Desktop, crane. Both base images are Alpine 3.24.1, so the
#    extracted musl CPython-3.12 .so and the copied Node binary are ABI-compatible.

# 1. Extract the frozen backend from the upstream image.
docker pull junoinnovations/genesis:v6.0.0
docker create --name g junoinnovations/genesis:v6.0.0
docker cp g:/src.app ./src.app; docker cp g:/prod/launch-prod.sh ./; docker rm g
docker run --rm -v "${PWD}:/work" -w /work python:3.12 sh -c `
  "pip install -q pyinstxtractor-ng && python -m pyinstxtractor_ng src.app"

# 2. Merge the split layout into one importable tree: pyinstxtractor puts pure-python
#    .pyc under PYZ.pyz_extracted/ and the .so in top-level package dirs.
docker run --rm -v "${PWD}:/work" python:3.12-alpine sh -c `
  "cp -r /work/src.app_extracted /work/app && `
   cp -r /work/src.app_extracted/PYZ.pyz_extracted/* /work/app/ && `
   rm -rf /work/app/PYZ.pyz_extracted"

# 3. Add ns_patch.py, run_backend.py, launch-prod.sh, Dockerfile (in this folder),
#    then build. --provenance=false keeps it a single pushable image.
docker buildx build --provenance=false --sbom=false --load -t genesis-lean:v6.0.0-ns4 .

# 4. Push to the Zot backend (same port-forward + robot-pusher as the mirror runbook).
kubectl -n zot-dev port-forward svc/zot 5000:5000   # leave running
$pw = (kubectl -n image-builds-dev get secret zot-push -o jsonpath='{.data.\.dockerconfigjson}' `
  | %{ [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($_)) } | ConvertFrom-Json `
  ).auths.'zot.dev.conductor.technology'.password
crane auth login localhost:5000 -u robot-pusher -p $pw --insecure
docker save genesis-lean:v6.0.0-ns4 -o g.tar
crane push g.tar localhost:5000/junoinnovations/genesis:v6.0.0-ns4 --insecure
crane auth logout localhost:5000; Remove-Item g.tar
```

Then bump `image.tag` in `values-dev.yaml` and let Argo sync.

The build inputs (`Dockerfile`, `ns_patch.py`, `run_backend.py`, `launch-prod.sh`,
`.dockerignore`) are committed under **`image/`** in this repo. They are the whole
source of truth for the rebuild; the large extracted bundle (`app/`, `src.app`,
`src.app_extracted`) is regenerated by steps 1-2 above and is intentionally **not**
committed. Copy `image/*` next to the generated `app/` to build.

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

## Runbook: sign-in + authorization

Genesis has two layers: **authentication** (NextAuth -- who you are) and
**authorization** (Rhea/cedar -- what you may do). Miss either and the UI shows an
empty sign-in page ("nada") or "You do not have the necessary permissions".

**1. Authentication -- enable a provider.** NextAuth only registers providers whose
env vars are set; with none, `/api/auth/providers` is `{}` and the sign-in page is
empty. Supported: `basic_auth` (`BASIC_AUTH_EMAIL`/`BASIC_AUTH_PASSWORD`, plus
`_2`,`_3`,... for more logins), `google` (`GOOGLE_CLIENT_ID/SECRET`), `cognito`
(`COGNITO_CLIENT_ID/SECRET/ISSUER`). The credentials provider posts one `formData`
field = JSON `{"input":"<email>","password":"<pw>"}`.

We use `basic_auth`, wired via `values-dev.yaml -> basicAuth` (toggle in `values.yaml`).
The password is **not** in git -- it lives in the `genesis-basic-auth` Secret
(key `password`), created out of band with kubectl create secret generic.

**2. Authorization -- map the identity to an admin.** `files/rhea/user-policies.cedar`
grants `principal in Group::"admin"` full access. Rhea maps the signed-in email to a
`User.juno-innovations.com` by `spec.email`, then checks its `Group` membership. A
fresh tenant only has the CoreWeave owner `jlehrman` (`jlehrman@coreweave.com`) in
`admin`, so any other login is denied. Create a matching User
(`kind: User`, `apiVersion: juno-innovations.com/v2`, `spec.email` = the login email,
`spec.active: true`, a unique `spec.uid`) and add its `metadata.name` to the `admin`
Group's `spec.members`.

> Rhea caches users/groups at pod start, so after creating users out-of-band you must
> **restart the genesis pod** (`kubectl -n genesis-dev delete pod -l app=genesis`) for
> the change to take effect. Normally users/groups are managed from the Genesis UI once
> an admin can log in. These CRs are `scope: Cluster` and were created imperatively (not
> in git); move them into GitOps if they must survive a cluster rebuild.

## Gotchas & lessons learned

Hard-won, non-obvious things from bringing this up. Read before touching the image,
auth, or RBAC.

**The Genesis backend is a closed PyInstaller binary.** No public source; the
`argocd` namespace and other behavior are baked in. Mirroring the upstream image with
crane does NOT give you editable source -- you have to extract the frozen bundle and
run it un-frozen (see the image runbook). `GENESIS_NAMESPACE` does not affect the
schema-cache lookup; only the `ns_patch.py` shim does.

**Alpine 3.24.1, but `apk add python3` = Python 3.14, not 3.12.** The genesis image
and `python:3.12-alpine` are both Alpine 3.24.1, yet the distro's `python3` package is
3.14 -- incompatible with the extracted musl CPython-3.12 `.so` (you get
`bad magic number`). Only `python:3.12-alpine` (Python built from source in
`/usr/local`) gives a matching 3.12 interpreter. Copy Node from the genesis image into
`python:3.12-alpine`, not the other way around.

**PyInstaller extraction layout is split.** `pyinstxtractor-ng` puts pure-Python
`.pyc` under `PYZ.pyz_extracted/` and compiled `.so` in top-level package dirs. You
must merge them into one tree or imports like `pydantic_core._pydantic_core` fail. The
backend also reads data files by absolute build-time path (`/app/src/static`,
`/app/src/backend/header/juno-ascii.txt`) that are NOT in the PyInstaller archive --
upstream ships them on the filesystem, so copy the whole `/app/src` from the image.

**Docker Desktop on Windows can't reach a host port-forward as `localhost`.** The
daemon runs in a VM, so `docker login/push localhost:5000` (a kubectl port-forward on
the host) times out. Use `crane` on the host (it hits `127.0.0.1:5000` directly):
`docker save img -o t.tar; crane push t.tar <ref> --insecure`. Build with
`--provenance=false --sbom=false` so you push a single image, not an attestation index.

**Next.js standalone binds to `$HOSTNAME`.** Kubernetes sets `HOSTNAME` to the pod
name (-> pod IP), so the frontend listens only there and the in-pod nginx TLS sidecar
(`proxy_pass 127.0.0.1:3000`) returns `502`. Launch node with `HOSTNAME=0.0.0.0`.
Upstream never hit this because it fronts the pod with an Ingress to the pod IP.

**NextAuth quirks.** `/api/auth/*` 308-redirects to add a trailing slash (the app sets
`trailingSlash: true`); a POST that does not follow 308 silently no-ops. The
`basic_auth` credentials provider expects a SINGLE `formData` field whose value is JSON
`{"input":"<email>","password":"<pw>"}` -- not separate `email`/`password` fields.
Providers are opt-in: with no provider env set, `/api/auth/providers` is `{}` and the
sign-in page is empty.

**Rhea / cedar authorization.** Cedar entity types are namespaced by the platform
namespace (`argocd::Service::"genesis"` in `system-policies.cedar`) -- another place the
"argocd" assumption surfaces, though for service principals it did not block us. User
identity is matched by `User.spec.email`; admin is `Group::"admin"` membership
(`permit(principal in Group::"admin", ...)`); `*/home/` is wide-open in policy. Rhea
caches users/groups at pod start -- restart the pod after any out-of-band User/Group
change.

**RBAC: Orion is not namespaced.** Its CRDs (groups/users/workstations/workstation-logs)
are `scope: Cluster`, and Genesis makes cluster-wide read calls at startup
(services across all namespaces, nodes, storageclasses, persistentvolumes,
customresourcedefinitions). Namespaced Roles cannot cover these, so a narrow read-only
ClusterRole is required -- but full cluster-admin is not.

**Licensing.** With no `juno-license` secret the backend logs `402: Failed to fetch
license token` and drops to **community mode** (2 workstations). That is enough to test
VDI; supply a real license for more.

**Operational.** The local working tree drifted to a stale version of tracked files at
one point while the committed branch and the live cluster were correct. Trust
`git show HEAD:<file>` and `kubectl get` over a local file read when they disagree.

## For prod later

The prod twin uses the same chart with a prod-shaped overlay: point the image registries
at `zot.conductor.technology` (mirror the same four images there), issue the cert for the
prod hostname, and add a `genesis-<ns>` entry to the prod cilium policies. Nothing about
the mirror/`zot-pull` procedure changes except the Zot host.
