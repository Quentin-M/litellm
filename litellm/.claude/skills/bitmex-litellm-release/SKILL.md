---
name: bitmex-litellm-release
description: Cut a new BitMEX LiteLLM release based on an upstream BerriAI tag. Covers rebasing BitMEX patches onto the new upstream tag (PR-bound fixes, security bumps, CI workflow, supervisor, OTel coercion), cherry-picking 0-day model support PRs from upstream, squashing commits per BitMEX convention, building & pushing the arm64 image to Tokyo ECR via the GitHub workflow, deploying to the gen-ai-dev instance via Helm + ArgoCD, validating end-to-end with Claude Code in Docker pointed at the dev proxy, then promoting to prod. Use when bumping LiteLLM to a new upstream version, when a new model lands upstream and needs to ship before the next release, or when the model catalog needs to be refreshed.
---

# BitMEX LiteLLM Release Procedure

End-to-end procedure for shipping a new BitMEX LiteLLM build, from upstream tag → arm64 image → dev validation → prod promotion. Applied for the v1.86.2-stable release (May 2026) and codifies the conventions discovered there.

This skill assumes Quentin's local checkout layout:

- LiteLLM fork: `~/Workspace/gen-ai/litellm/image` (remotes: `upstream`=BerriAI/litellm, `bmex`=BitMEX-private/litellm)
- gen-ai infra repo: `~/Workspace/gen-ai`
- Cluster context: `kubectl --context=shared-global` for both dev (`gen-ai-dev`) and prod (`gen-ai`) namespaces

## Conventions (must follow)

### Branch naming

- Stable release branch: `bitmex-v<MAJOR>.<MINOR>.<PATCH>-stable` (e.g. `bitmex-v1.86.2-stable`)
- Nightly cut against upstream main: `bitmex-v<version>-nightly`
- Throwaway PR branches inside the fork: `fix/<topic>` or `feat/<topic>`
- Deploy PR branches in `BitMEX-private/gen-ai`: `deploy/litellm-<env>-v<version>` (e.g. `deploy/litellm-dev-v1.86.2`)

### Commit ordering

PR-bound fixes go FIRST (lowest in history), so they form a clean diff against upstream when re-submitted. BitMEX-only patches layer on top. The release branch always looks like:

```
<top — squashed BitMEX-only patches>
chore(deps): security dependency bumps [PLACEHOLDER]
chore(bitmex): replace upstream CI ... + restore supervisor
fix(otel): coerce None custom_llm_provider to Unknown
<PR-bound commit 4>
<PR-bound commit 3>
<PR-bound commit 2>
<PR-bound commit 1 — bottom of branch, first cherry-pick>
v<X.Y.Z> upstream tag
```

### Squashing strategy

Squash opportunistically to minimize future rebase pain. Specifically:

- **All dependency bumps for a release** → 1 squashed commit, subject ends with `[PLACEHOLDER]` (see Phase 2d). Dep files are conflict magnets; one logical commit beats N conflict resolutions.
- **Cherry-picked upstream PR for 0-day model support** → 1 squashed commit, with reviewer fixes (e.g. greptile) applied as part of the squash (Phase 2e).
- **Closely-coupled infra commits** (CI replace + a follow-up CI tweak; Dockerfile change + supervisor restore) → squash if they always travel together (see Phase 2c "opportunistic squashing").

Don't squash unrelated concerns or anything you might need to revert independently.

### Pricing/model catalog

Default behavior — leave alone: the proxy fetches `https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json` at startup. Upstream main is the source of truth, which is what we want for routine releases.

Override only when shipping 0-day support for a new model (Phase 2e). When you cherry-pick an open upstream PR adding model entries, the upstream `main` JSON does NOT yet have them, so the runtime fetch will overwrite your cherry-picked entries. In that case (and only that case), set `LITELLM_LOCAL_MODEL_COST_MAP: "true"` in the Helm values' env block (NOT in the `Dockerfile` ENV — keep the image clean so it stays usable across deploys without behavior surprises). Revert this Helm setting in the next release once upstream main has merged the model entries.

## Phase 0: Inventory current state

Before starting, dump everything you'll touch.

```bash
cd ~/Workspace/gen-ai/litellm/image
git fetch upstream --prune --tags
git fetch bmex
git fetch upstream pull/<PR>/head:pr-<PR>   # for each upstream PR you'll cherry-pick

# Walk BitMEX-only patches on top of upstream main
git log --oneline upstream/main..bmex/bitmex --no-merges
# Dedupe by patch-id (sync-rebase merges produce dupes):
git log --no-merges --format='%H' upstream/main..bmex/bitmex | while read sha; do
  pid=$(git show $sha | git patch-id | awk '{print $1}')
  printf "%s  %s\n" "${pid:0:12}" "$(git log -1 --format='%h %s' $sha)"
done | sort
```

Categorize each commit into:

1. **PR-bound** (pending upstream PR — e.g. BerriAI/litellm#23523)
2. **BitMEX-only** (CI workflow, supervisor, OTel coercion, security bumps)
3. **0-day model support** (cherry-pick from open upstream PRs adding new models — e.g. BerriAI/litellm#29204 for Opus 4.8)
4. **Already in target tag** (verify via `git show <tag>:<file>` — drop these)
5. **Patch-id duplicates** from prior rebase merges (drop)

Verify each PR-bound and BitMEX-only commit is still needed against the new tag. For each commit's "before" state, search the new tag — if it's already fixed upstream, drop. Use a sub-agent for this audit; produces a STILL NEEDED / ALREADY FIXED verdict per commit.

## Phase 1: Cut the release branch from upstream tag

```bash
TAG=v1.86.2   # set to your target upstream tag
git checkout -b bitmex-v${TAG#v}-stable $TAG
```

## Phase 2: Apply patches in order

Order matters — PR-bound first so the diff against upstream stays clean.

### 2a. PR-bound fixes (BerriAI/litellm#23523)

```bash
git cherry-pick <sha1> <sha2> <sha3> <sha4>
```

If conflicts: resolve, `git add`, `git cherry-pick --continue`. Don't squash these — keep them as 1 commit each so they stay extractable for the upstream PR.

### 2b. Standalone BitMEX-only fixes

Apply each BitMEX-only fix that didn't make it into the upstream PR (e.g. observability fixes like the OTel `custom_llm_provider` None coercion, runtime guards, log noise reduction). One commit each — keep them as individual commits for easier picking/dropping in future rebases:

```bash
git cherry-pick <fix-sha-1>
git cherry-pick <fix-sha-2>
# ...
```

If a fix you previously carried is now in the upstream tag (`git show <tag>:<file>` shows the patched code), drop it. Re-run the inventory check from Phase 0 if you weren't sure.

### 2c. Infra-shaping commits (CI, Dockerfile, supervisor, …)

Apply BitMEX-only commits that reshape build/runtime infrastructure: CI workflow replacement, Dockerfile changes, supervisor config, image-baked env vars, etc.

Many of these touch files upstream actively edits (workflows, Dockerfile). Expect modify/delete or content conflicts. Resolve them in line with each commit's intent — e.g., the CI-replace commit deletes upstream workflow files; if upstream modified one of them in the new tag, the right resolution is still to delete:

```bash
git cherry-pick <sha>
# If conflicts, e.g. modify/delete:
git status --short | grep '^UD' | awk '{print $2}' | xargs git rm
git -c core.editor=true cherry-pick --continue
```

#### Opportunistic squashing

After a few releases, related BitMEX-only commits drift apart in history and re-conflict on every rebase. Squash opportunistically to cut future conflict surface — but only when squashing won't make a future split harder. Good candidates:

- Two commits that touch the same files with no daylight (e.g. CI replace + a follow-up CI tweak)
- A commit and its bug-fix follow-up
- A multi-file refactor split across N small commits for review purposes — collapse once landed

Bad candidates (keep separate):

- Commits owned by different people or different concerns (security bumps vs CI)
- Commits that may need to be reverted independently
- Commits that you might extract into an upstream PR later

When you do squash, soft-reset and commit with a body that lists what was squashed (so future you can split it back out):

```bash
git reset --soft HEAD~N
git commit -m "<combined-subject>

Squashed from:
- <subject of original commit 1>
- <subject of original commit 2>

<combined body>"
```

For the security-bumps commit specifically, append `[PLACEHOLDER]` to the subject and explain the squash is for rebase ergonomics — see 2d below.

### 2d. BitMEX dependency bumps (single squashed commit, [PLACEHOLDER] subject)

Always collapse ALL BitMEX-side dependency bumps (security CVEs, npm/pip pins, base image digest pins, etc.) into ONE squashed commit per release. Dependency files (`pyproject.toml`, `uv.lock`, `package.json`, `package-lock.json`, `Dockerfile` ARGs) are conflict magnets: every upstream rebase re-touches them, and N separate bump commits = N conflict resolutions for what's effectively one logical change.

```bash
# Cherry-pick every dependency bump
git cherry-pick <bump-sha-1> <bump-sha-2> <bump-sha-3> ...

# Squash into one
git reset --soft HEAD~N
git commit -m "chore(deps): security dependency bumps [PLACEHOLDER]

Squashed to ease conflict resolution on rebases. Always squash all
BitMEX-side dependency bumps for a release into this one commit.
Replace per-bump if a stable cadence is ever established.

Squashed from:
- chore(deps): bump next 16.X.Y → 16.X.Z (security) — CVEs: ...
- security: bump fastapi>=X.Y.Z + pin starlette>=A.B.C (Host header
  injection) — Starlette < 1.0.1 derives request.url from the Host
  header without sanitization, allowing path-based auth-middleware
  bypass.
- chore(deps): bump <package> <old> → <new> (...)"
```

Drop bumps that are now redundant (the new upstream tag already includes the version we wanted). Use `git show <tag>:pyproject.toml | grep <pkg>` etc. to verify before squashing.

### 2e. 0-day model support (cherry-pick open upstream PR)

When a new model lands upstream but isn't in your target tag (e.g. Opus 4.8 in PR #29204), cherry-pick it. Skip noisy commits (e.g. full backup-file regenerations) and patch the relevant entries surgically:

```bash
git fetch upstream pull/29204/head:pr-29204

# Cherry-pick the focused commits (root JSON, constants, wizard, tests)
git cherry-pick <root-json-sha> <constants-sha> <wizard-sha> <test-sha>

# Skip the bulk-regen commit (model_prices_and_context_window_backup.json)
# — it carries unrelated drift. Sync the new model entries to the backup
# JSON surgically with a Python regex script (see scripts/port_model_entries.py).

# Apply any greptile / reviewer fixes the upstream PR author hasn't addressed
# (e.g. provider_specific_entry list-of-arrays → dict format).

# Squash the model entries into one commit:
git reset --soft <pre-cherry-pick-sha>
git commit -m "feat(models): add Claude Opus X.Y

Cherry-picked from BerriAI/litellm#XXXXX (LIT-NNNN), with adjustments:

1. Fix provider_specific_entry shape — upstream PR stored it as
   list-of-arrays but cost_calculation.py calls .get() as a dict.
   Per greptile-apps[bot] review (discussion rXXXXXXXX).

2. Sync new entries into model_prices_and_context_window_backup.json
   for the bundled local catalog. Skipped upstream commit YYY (full
   backup regen) — too noisy with unrelated drift."
```

Then in the deploy PR (Phase 5), set `LITELLM_LOCAL_MODEL_COST_MAP: "true"` in the Helm values' env block so the bundled local catalog wins over the upstream-main fetch. Remove that Helm setting in the next release once upstream has merged the model PR.

See `scripts/port_model_entries.py` for the surgical port pattern.

## Phase 3: Push to bmex

```bash
git push -u bmex bitmex-v${TAG#v}-stable
```

## Phase 4: Build the arm64 image

The push triggers `.github/workflows/main.yaml` automatically (push to `bitmex-*` branches). Monitor:

```bash
gh run list --repo BitMEX-private/litellm --branch bitmex-v${TAG#v}-stable --limit 3
gh run watch <run-id> --repo BitMEX-private/litellm --exit-status
```

Verify the runner is `private-small-arm` (native arm64), NOT `ubuntu-latest` (which would be amd64+QEMU emulation, slow & risky):

```bash
gh api repos/BitMEX-private/litellm/actions/runs/<run-id>/jobs \
  | jq '.jobs[] | {name, runner_name, labels}'
```

Build typically takes 9–10 minutes on the native runner. The image tag is the full git SHA of the branch HEAD: `770953171216.dkr.ecr.ap-northeast-1.amazonaws.com/litellm:<full-sha>`. ECR tags are immutable.

## Phase 5: Deploy to dev

### 5a. Add non-mTLS apiGateway (one-time per env)

The `litellm-dev.shared.global.bitmex` host uses mTLS via `istio-gateways/private-mtls`. For Claude Code (no client cert), you need a non-mTLS endpoint. Mirror prod's pattern by adding `apiGateway` to `helm/litellm/instances/shared-global/gen-ai-dev/values.yaml`:

```yaml
ingress:
  enabled: true
  domain: litellm-dev.shared.global.bitmex
  gateway:
    selector: istio-gateways/private-mtls
    deploy: false
  apiGateway:
    enabled: true
    domain: litellm-api-dev.shared.global.bitmex
    selector: istio-gateways/private
    deploy: false
```

The chart template falls through to LiteLLM's built-in bearer auth when XFCC is absent (see `helm/litellm/templates/litellm.yaml`, the `# Non-mTLS VirtualService` block).

### 5b. Bump dev image tag

In the same PR:

```yaml
image:
  repository: 770953171216.dkr.ecr.ap-northeast-1.amazonaws.com/litellm
  tag: <full-sha-of-new-image>
```

### 5c. PR + merge

```bash
cd ~/Workspace/gen-ai
git checkout -b deploy/litellm-dev-v${TAG#v} origin/master
# edit values.yaml (image bump + apiGateway block if first time)
git commit -am "deploy(litellm-dev): bump to v${TAG#v}-stable + add non-mTLS apiGateway"
git push -u origin deploy/litellm-dev-v${TAG#v}
gh pr create --reviewer jhob --title "deploy(litellm-dev): bump to v${TAG#v}-stable" --body "..."
```

### 5d. Nudge ArgoCD

ArgoCD on shared-global has auto-sync, BUT it can be misleading: a manual `helm.parameters` override on the Application can shadow the values.yaml change. Always check + clear:

```bash
# 1. Check current Application spec for parameter overrides
kubectl --context=shared-global -n gen-ai-dev get app litellm.gen-ai-dev -o json \
  | jq '.spec.source.helm.parameters'

# 2. If the output is non-null and pins image.tag (or anything you set in values.yaml),
#    remove it (manual override added via ArgoCD UI/CLI on a prior deploy):
kubectl --context=shared-global -n gen-ai-dev patch app litellm.gen-ai-dev \
  --type=json -p='[{"op": "remove", "path": "/spec/source/helm/parameters"}]'

# 3. Hard refresh to pull the new git revision
kubectl --context=shared-global -n gen-ai-dev annotate app litellm.gen-ai-dev \
  argocd.argoproj.io/refresh=hard --overwrite

# 4. Confirm sync revision matches your merge commit
kubectl --context=shared-global -n gen-ai-dev get app litellm.gen-ai-dev -o json \
  | jq '{sync: .status.sync.status, revision: .status.sync.revision[:12], health: .status.health.status}'

# 5. Watch rollout
kubectl --context=shared-global -n gen-ai-dev rollout status deploy/litellm
kubectl --context=shared-global -n gen-ai-dev get deploy litellm \
  -o jsonpath='{.spec.template.spec.containers[*].image}{"\n"}'
```

The deployment image must match your new SHA. If not, re-check parameter overrides.

### 5e. Register new models (only if Phase 2e applied)

Dev (and prod) have `store_model_in_db: true` — model routes live in Postgres, NOT in `config.yaml`. Bumping the image ships pricing data, but the model has to be added to the proxy's `model_list` separately, or `/v1/messages` calls will return `Invalid model name passed in model=X`.

Register the new model via the LiteLLM dashboard:

- Dashboard URL (dev): https://litellm-dev.shared.global.bitmex (mTLS-required path) — log in with the dev master key, navigate to **Models → Add Model**
- Pick the canonical model id from the catalog (e.g. `claude-opus-4-8`)
- Add aliases the clients will actually send. Claude Code typically sends `claude-opus-4-8`, `claude-sonnet-4-X`, `claude-haiku-4-X` — register any alias that doesn't already resolve via `/v1/models`.
- Set the bedrock backend (e.g. `bedrock/global.anthropic.claude-opus-4-8-v1:0`) and any required params

Verify aliases land:

```bash
DEV_KEY="$(kubectl --context=shared-global -n gen-ai-dev get secret litellm -o jsonpath='{.data.LITELLM_MASTER_KEY}' | base64 -d)"
curl -sS -H "Authorization: Bearer $DEV_KEY" \
  https://litellm-api-dev.shared.global.bitmex/v1/models | jq '.data[].id'
```

The new model ids should appear. If a client sends an alias not in this list, the proxy 400s — model registration drift is the #1 cause of "claude is timing out" reports during validation.

## Phase 6: Validate against dev with real Claude Code

The dev `litellm-api-dev.shared.global.bitmex` endpoint accepts bearer auth without mTLS. Master key lives in K8s secret `litellm` (key `LITELLM_MASTER_KEY`), populated by ExternalSecret from AWS Secrets Manager `shared-global/gen-ai-dev/litellm`.

Run `claude` inside Docker pointed at the dev proxy. The script + Dockerfile are bundled with this skill — see `scripts/run-claude-dev.sh` and `scripts/Dockerfile.claude-dev`.

```bash
SKILL=litellm/.claude/skills/bitmex-litellm-release/scripts

# Build once (stages BitMEX CA from a known host path; see build-claude-dev.sh)
$SKILL/build-claude-dev.sh

# Run interactive
$SKILL/run-claude-dev.sh

# One-shot test
$SKILL/run-claude-dev.sh -p "say 'litellm dev ok'"
```

The script:
- Pulls `LITELLM_MASTER_KEY` from K8s secret on the fly (no secret on disk)
- Mounts `$PWD` as `/work` and `~/Workspace` as `/root/Workspace` for cross-repo testing
- Persists Claude state to `~/.config/claude-litellm-dev/state/` (NOT host `~/.claude` — that has prod OAuth)
- Sets ONLY `ANTHROPIC_BASE_URL` + `ANTHROPIC_AUTH_TOKEN`. Don't add `ANTHROPIC_MODEL` / default-model env vars unless the model alias really exists in the proxy's `/v1/models` output. Claude Code's defaults work as long as the proxy exposes `claude-haiku-*`, `claude-sonnet-*`, `claude-opus-*` aliases.

Smoke checks:

```bash
# 1. /v1/models returns a list including any new models you added
DEV_KEY="$(kubectl --context=shared-global -n gen-ai-dev get secret litellm -o jsonpath='{.data.LITELLM_MASTER_KEY}' | base64 -d)"
curl -sS -H "Authorization: Bearer $DEV_KEY" \
  https://litellm-api-dev.shared.global.bitmex/v1/models | jq '.data[].id'

# 2. /v1/messages round-trip (replace model alias with what /v1/models returned)
curl -sS -H "Authorization: Bearer $DEV_KEY" \
  -H 'Content-Type: application/json' \
  -H 'anthropic-version: 2023-06-01' \
  https://litellm-api-dev.shared.global.bitmex/v1/messages \
  -d '{"model":"<your-alias>","max_tokens":50,"messages":[{"role":"user","content":"reply with: dev ok"}]}'

# 3. Tail proxy logs for backend routing (filter health probe noise)
kubectl --context=shared-global -n gen-ai-dev logs deploy/litellm --tail=200 \
  | grep -vE 'health/livel|Malformed API Key|user_api_key_auth'
```

### Common dev validation pitfalls

- **`Invalid model name passed in model=X`**: the alias the client sends doesn't match what's stored in the LiteLLM Postgres `model_list` table (dev has `store_model_in_db: true`). New models added via the LiteLLM dashboard, NOT by image. Check `/v1/models` for the actual alias.
- **Spend log `TypeError: keys must be str, int, float, bool or None, not tuple`**: log noise on the failure path of `ProxyModelNotFoundError`. Doesn't break responses. Upstream bug, separate fix.
- **`Malformed API Key` in logs**: istio probes hitting `/test` without bearer. Expected when migrating from mTLS-only to bearer.
- **Three-way config divergence**: model routes can live in K8s env vars, `config.yaml` ConfigMap, AND Postgres `LiteLLM_Config`. They don't sync. When a route doesn't show up, check all three.

## Phase 7: Promote to prod

After dev soak passes (smoke + Claude Code + a few real workloads):

```bash
cd ~/Workspace/gen-ai
git checkout -b deploy/litellm-prod-v${TAG#v} origin/master
# edit helm/litellm/instances/shared-global/gen-ai/values.yaml — bump image.tag only
# (apiGateway already exists in prod values.yaml; don't touch it)
git commit -am "deploy(litellm): bump prod to v${TAG#v}-stable"
gh pr create --reviewer jhob --title "deploy(litellm): bump prod to v${TAG#v}-stable" --body "..."
```

Repeat the ArgoCD nudge in Phase 5d but for app `litellm.gen-ai` in namespace `gen-ai`. Prod has 3 replicas — rolling deploy takes longer; watch each pod come up healthy before declaring success.

If Phase 2e applied (new-model 0-day support), repeat Phase 5e against prod: register the new model + aliases via the prod LiteLLM dashboard (`https://litellm.shared.global.bitmex` or the keygen portal at `https://litellm-keygen.shared.global.bitmex`), and verify they appear in `/v1/models` against `https://litellm-api.shared.global.bitmex` with the prod master key (AWS Secrets Manager `shared-global/gen-ai/litellm`, K8s secret `litellm` in `gen-ai` namespace).

## Repository naming reference

| Remote | URL | Purpose |
|---|---|---|
| `upstream` | git@github.com:BerriAI/litellm.git | Upstream OSS source. PRs go here. |
| `bmex` | git@github.com:BitMEX-private/litellm.git | BitMEX fork — release branches live here. |

Docker registry: `770953171216.dkr.ecr.ap-northeast-1.amazonaws.com/litellm` (Tokyo, profile `cicd`).

## Files maintained by this skill

- `scripts/Dockerfile.claude-dev` — minimal Claude Code container with BitMEX CA installed
- `scripts/run-claude-dev.sh` — wrapper that pulls master_key from K8s and launches the container
- `scripts/port_model_entries.py` — surgical JSON port for new-model 0-day cherry-picks
