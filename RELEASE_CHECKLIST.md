# Release checklist

Steps for tagging a new Hayavega version. Nothing here enforces itself
automatically yet (see "Not yet enforced" below) -- this is a manual
checklist, not a CI gate.

1. **Update `CHANGELOG.md`.** Move the `[Unreleased]` section's entries
   under a new `## [X.Y.Z] - YYYY-MM-DD` heading; start a fresh empty
   `[Unreleased]` section above it.
2. **Review `SECURITY_REVIEW_CHECKLIST.md`.** Confirm every control still
   has a passing verification test, and record any accepted exception
   with an owner and expiry date before releasing with it outstanding.
3. **Tag the release**: `git tag vX.Y.Z` on the commit that will ship,
   then `git push origin vX.Y.Z`.
4. **Watch the `publish`/`release` GitHub Actions workflows** (trusted
   publishing to TestPyPI/PyPI) complete successfully.
5. **Create a GitHub Release for the tag**, with notes drawn from the
   `CHANGELOG.md` entry just added. (As of `v0.1.0`, this step is
   deliberately not automated -- see `NEXT_RELEASE_ROADMAP.md`'s Phase 1
   for why it was held back for that release specifically.)
6. **Confirm the live docs site redeployed.** Pushing the tag is what
   triggers it: `vercel.json`'s `ignoreCommand` gates deploys to tagged
   commits only, so an ordinary branch merge does not touch
   `https://hayavega.vercel.app/` -- only this step does.

## Not yet enforced

Nothing above runs automatically today. The real gap this checklist
exists to close is step 2 in particular (`SECURITY_REVIEW_CHECKLIST.md`
already says "review this checklist before each production release," but
nothing currently checks that a release actually happened without
skipping it). Making any of these steps a real CI gate -- e.g. a
release workflow that refuses to tag without a matching `CHANGELOG.md`
entry -- is open follow-up work, not claimed as done here.
