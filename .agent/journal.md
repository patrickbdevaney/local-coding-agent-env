
## 2026-08-25 17:03 — Full git cycle: branch, commit, push, PR, merge, delete

Ran the full git cycle end-to-end on 2026-08-25: branch skill/version-control, commit, push, PR #1, merge, delete branch.

**What worked:**
- `git checkout -b`, `git add`, `git commit`, `git push -u origin` — all clean.
- `gh pr create` failed with "GraphQL: API rate limit already exceeded" (5000/5000 used, ~30 min to reset). Workaround: `gh api repos/.../pulls -X POST -f base=main -f head=... -f title=... -f body=...` — the REST API has a separate 5000/hour quota and was at 0/5000.
- `gh api repos/.../pulls/1/merge -X PUT` — merged cleanly.
- `gh api repos/.../branches/skill/version-control -X DELETE` returned 404 (branch already gone from GitHub's side after merge). `git push origin --delete skill/version-control` worked for the remote ref.
- `git branch -d` failed while on the branch itself; had to `git checkout main` first.

**CI:** No GitHub Actions workflows exist in this repo (`/actions/runs` returns 404, `total_count: 0`). No checks to wait on.

**Commit style:** Repo uses one-line imperative subjects with a body explaining why. Matched that.

**PR:** #1 — https://github.com/patrickbdevaney/local-coding-agent-env/pull/1
