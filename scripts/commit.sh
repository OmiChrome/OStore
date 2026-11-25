#!/usr/bin/env bash
# Commit the given paths as the bot and push, retrying if main moved meanwhile.
# Usage: scripts/commit.sh "message" path...
set -euo pipefail
cd "$(dirname "$0")/.."
msg=$1
shift
git config user.name "github-actions[bot]"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
git add -- "$@"
if git diff --cached --quiet; then
	echo "nothing to commit"
	exit 0
fi
git commit -q -m "$msg"
for i in 1 2 3 4; do
	if git push -q origin HEAD:main; then exit 0; fi
	echo "push failed, rebasing and retrying ($i)"
	sleep $((i * 5))
	git pull -q --rebase origin main
done
echo "::error::could not push $msg"
exit 1
