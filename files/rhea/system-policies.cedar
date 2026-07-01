// Service-to-service (system) authorization.
//
// Upstream ships one rule per service, each scoped to `resource ==
// argocd::Service::"<svc>"`. That `argocd` prefix is the cedar namespace rhea
// derives from the PLATFORM NAMESPACE, and upstream installs into a namespace
// literally named "argocd". We run in "genesis-dev": with NAMESPACED=true rhea
// builds the resource entity as `genesis-dev::Service::"<svc>"`, which
//   (a) never equals `argocd::Service::"<svc>"`, so every shipped rule misses
//       and every service-to-service call is denied (403 "RHEA_AUTHZ:
//       Authorization denied" on e.g. GET /titan/users, /titan/groups), and
//   (b) cannot even be written back into policy -- "genesis-dev" has a hyphen,
//       an illegal cedar identifier (`parser error ... "dev": want ::`).
//
// The principal itself IS established correctly (rhea sets
// principal.serviceAccountName from the caller's projected SA token -- verified:
// a resource-free rule gated only on serviceAccountName returns 200). So we gate
// on the caller identity instead of the (unexpressible) resource entity. This
// keeps NAMESPACED=true (user/group auth is unchanged) and authorizes exactly
// the trusted platform service accounts the per-service rules used to enumerate.
// See INTEGRATION.md "Rhea / cedar authorization".
permit (
  principal,
  action in
    [Action::"GET",
     Action::"POST",
     Action::"PUT",
     Action::"DELETE",
     Action::"PATCH"],
  resource
)
when
{
  principal.serviceAccountName == "genesis" ||
  principal.serviceAccountName == "orion-hubble" ||
  principal.serviceAccountName == "kuiper" ||
  principal.serviceAccountName == "titan" ||
  principal.serviceAccountName == "metrics-gatherer" ||
  principal.serviceAccountName == "terra"
};
