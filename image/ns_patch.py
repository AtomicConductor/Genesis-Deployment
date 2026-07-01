"""Runtime parametrization of Genesis's platform namespace.

Upstream Genesis (closed, shipped as a PyInstaller binary) hardcodes the string
"argocd" as the namespace for its platform resources (schema-cache configmaps and
workload-template custom objects) -- see backend/workloads/service.py Config.*.
That assumes Genesis is installed INTO a namespace literally named "argocd".

We run Genesis in its own namespace, so this shim rewrites those calls at the
kubernetes client layer: any namespaced API call whose namespace == "argocd" is
redirected to the platform namespace, resolved (in order) from:
  1. $GENESIS_PLATFORM_NAMESPACE
  2. $GENESIS_NAMESPACE
  3. the pod's own service-account namespace file
  4. "argocd" (unchanged -> upstream behavior)

Only the exact value "argocd" is rewritten, so calls that legitimately target
other namespaces (e.g. per-user project namespaces) are never touched. Idempotent.
"""

import functools
import os

_HARDCODED = "argocd"
_SA_NS_FILE = "/var/run/secrets/kubernetes.io/serviceaccount/namespace"


def _platform_namespace():
    for var in ("GENESIS_PLATFORM_NAMESPACE", "GENESIS_NAMESPACE"):
        val = os.environ.get(var)
        if val:
            return val
    try:
        with open(_SA_NS_FILE, "r", encoding="utf-8") as handle:
            val = handle.read().strip()
            if val:
                return val
    except OSError:
        pass
    return _HARDCODED


def _rewrite(path_params):
    if path_params is None:
        return path_params
    target = _platform_namespace()
    if target == _HARDCODED:
        return path_params
    if isinstance(path_params, dict):
        if path_params.get("namespace") == _HARDCODED:
            path_params = dict(path_params)
            path_params["namespace"] = target
        return path_params
    try:
        rewritten = []
        changed = False
        for key, value in path_params:
            if key == "namespace" and value == _HARDCODED:
                rewritten.append((key, target))
                changed = True
            else:
                rewritten.append((key, value))
        return rewritten if changed else path_params
    except (TypeError, ValueError):
        return path_params


def _patch(module_name):
    try:
        module = __import__(module_name, fromlist=["ApiClient"])
    except Exception:
        return False
    api_client = module.ApiClient
    original = api_client.call_api
    if getattr(original, "_genesis_ns_patched", False):
        return True

    @functools.wraps(original)
    def call_api(self, resource_path, method, path_params=None, *args, **kwargs):
        if "path_params" in kwargs:
            kwargs["path_params"] = _rewrite(kwargs["path_params"])
            return original(self, resource_path, method, path_params, *args, **kwargs)
        return original(self, resource_path, method, _rewrite(path_params), *args, **kwargs)

    call_api._genesis_ns_patched = True
    api_client.call_api = call_api
    return True


def apply():
    patched = []
    for module_name in (
        "kubernetes_asyncio.client.api_client",
        "kubernetes.client.api_client",
    ):
        if _patch(module_name):
            patched.append(module_name)
    return patched


apply()
