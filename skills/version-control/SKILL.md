---
name: version-control
description: Do git work and open GitHub pull requests — branching, committing, pushing, PRs, CI, merging. Use whenever a change is going into this repository or any other git repo, or when the user asks for a PR, a merge, or where a change stands in review.
---

# Doing git work

A commit is a claim that the tree is in a state worth keeping. Everything below
serves that: the branch isolates the claim, the message explains it, the PR
puts it in front of a check, and the merge retires it.

## Branch, don't commit to main

```bash
git checkout -b skill/<short-name>   # or fix/<short-name>, feat/<short-name>
```

Committing directly to main skips the review and CI steps that exist to catch
your mistakes before they are shared. If the user asked for a change, the
default is a branch named for the change, not a commit on the default branch.
Only commit to main when the user explicitly says so, or when the repo's
convention is clearly trunk-based and the change is trivial.

## Before committing: look at what you actually changed

```bash
git status          # what is staged, what is not, what is untracked
git diff            # the unstaged changes
git diff --staged   # what the commit will actually contain
```

Read your own diff before committing. Staging the wrong file, committing a
half-finished edit, or including a file you meant to leave out are all visible
in thirty seconds of reading and invisible for a long time afterwards. If the
diff contains anything you do not recognise, stop and figure out where it came
from before it becomes part of history.

## Never commit secrets

Keys, tokens, passwords, connection strings, and anything in a `.env` file do
not go in a commit. A commit is permanent: rewriting history to remove a
secret is possible but painful, and the secret is already leaked the moment it
is pushed. If you find a secret in a diff, remove it, and if it was ever
pushed, say so plainly and treat the credential as compromised.

## Write the message for the reader six months from now

The diff shows *what* changed. The message has to say *why* — what was wrong,
what the change does about it, and what you rejected if there was a choice.

```
<what changed, in the imperative, one line>

<why it was needed, and what the alternative was, if any>
```

Match the repository's existing style: run `git log --oneline -20` first and
copy its tense, length, and punctuation. A repo that uses one-line imperative
subjects does not want a paragraph; a repo that writes full sentences does not
want a fragment. The message is for the next person, not for you.

## Open the PR

```bash
git push -u origin <branch>
gh pr create --title "<same as the commit subject>" --body "<the why>"
```

The PR body is the commit message's why, expanded: what the change does, why,
and how it was verified. A reviewer should not have to read the diff to
understand the intent.

## Check CI before merging

```bash
gh pr checks <number>
```

Do not merge on "it passed locally". The checks are the only evidence that the
change works in the environment that will actually run it. If a check fails,
fix the change — not the check — unless the check is genuinely wrong, in which
case say so and fix the check in the same PR.

## Merge and clean up

```bash
gh pr merge <number> --merge --delete-branch
git checkout main && git pull
```

Delete the branch after merging. A branch that has already landed is clutter;
keeping it around implies the work is still open.

## When `gh pr create` fails on a rate limit

Measured, first time this skill was exercised: `gh pr create` returned
`GraphQL: API rate limit already exceeded` at 5000/5000, with half an hour to
reset. The `gh` porcelain commands go through GraphQL; the REST API has a
**separate** quota, and it was at 0/5000 at the same moment. So this works when
the porcelain does not:

```bash
gh api repos/OWNER/REPO/pulls -X POST \
  -f base=main -f head=BRANCH -f title=TITLE -f body=BODY
gh api repos/OWNER/REPO/pulls/N/merge -X PUT
```

Check which quota you have actually exhausted before waiting:

```bash
gh api rate_limit --jq '{graphql:.resources.graphql, core:.resources.core}'
```

Do not sit out a thirty-minute reset for a limit that does not apply to the call
you need.

## The traps

- **Never force-push a shared branch.** `git push --force` rewrites history
  that other people have built on top of. It is fine on a private branch you
  own and have not pushed, or with `--force-with-lease` on your own feature
  branch. It is never fine on main, and it is never fine on a branch someone
  else has pulled.
- **Never commit secrets.** See above. This is the one that cannot be undone.
- **Check `git status` and read your own diff before committing.** The thirty
  seconds that catch a staged `.env` or a half-finished file are cheaper than
  the hour it takes to unwind a bad commit.
- **Match the repository's commit style.** `git log --oneline -20` tells you
  what the repo does. A message that reads like it was written by a different
  tool in a different project is a small signal that the rest of the change
  may not have been read either.

- **`git branch -d` fails while you are standing on that branch.** `git checkout
  main` first. Obvious in hindsight, easy to trip over inside a script.
- **After a merge, GitHub may already have deleted the remote branch.** A
  follow-up delete returning 404 is success, not failure — check before treating
  it as an error.
- **Privileged operations.** This user's machines are configured for passwordless
  sudo, so `sudo` works unattended — which means a wrong command runs unattended
  too. Say what you are about to do before doing anything destructive with it,
  and never pipe an unreviewed script into a privileged shell.
