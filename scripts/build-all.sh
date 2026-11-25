#!/usr/bin/env bash
# Build every app given as arguments.
#
# 1. Build with the pinned engine (engine.lock).
# 2. Apps that failed get one more try with the newest upstream engine.
#    If that works, the pin moves forward by itself (upstream fixed it).
# 3. Good builds are released; failures open or update a GitHub issue.
set -uo pipefail
cd "$(dirname "$0")/.."

APPS=("$@")
OST="python3 scripts/ostore.py"
ok=() failed=()

PIN=$($OST engine --dir engine | sed -n 's/^sha=//p')
[ -n "$PIN" ] || { echo "::error::could not fetch the pinned engine"; $OST issue-fail engine --step fetch-engine --message "Could not download the pinned j-hc engine."; exit 1; }

for app in "${APPS[@]}"; do
	echo "::group::Build $app (pinned engine ${PIN:0:10})"
	if $OST build "$app" --engine engine --engine-sha "$PIN"; then ok+=("$app"); else failed+=("$app"); fi
	echo "::endgroup::"
done

if [ ${#failed[@]} -gt 0 ]; then
	HEAD_SHA=$($OST engine --dir engine-head --sha HEAD | sed -n 's/^sha=//p')
	if [ -n "$HEAD_SHA" ] && [ "$HEAD_SHA" != "$PIN" ]; then
		still=() bumped=false
		for app in "${failed[@]}"; do
			echo "::group::Retry $app with newest upstream engine ${HEAD_SHA:0:10}"
			if $OST build "$app" --engine engine-head --engine-sha "$HEAD_SHA"; then ok+=("$app"); bumped=true; else still+=("$app"); fi
			echo "::endgroup::"
		done
		failed=("${still[@]}")
		if [ "$bumped" = true ]; then
			sed -i "s/^JHC_COMMIT=.*/JHC_COMMIT=${HEAD_SHA}/" engine.lock
			echo "Engine pin moved to ${HEAD_SHA} because it fixed a failing build."
		fi
	else
		echo "Newest upstream engine is the same as the pin; no retry."
	fi
fi

for app in "${ok[@]}"; do
	if $OST release "$app"; then
		$OST record "$app"
		$OST issue-ok "$app" || true
	else
		failed+=("$app")
		mkdir -p "out/$app"
		[ -f "out/$app/error.json" ] || echo '{"step":"release","message":"Uploading the GitHub release failed"}' >"out/$app/error.json"
	fi
done

for app in "${failed[@]}"; do
	$OST issue-fail "$app" || echo "::warning::could not open the failure issue for $app"
done

{
	echo "### Build summary"
	echo ""
	for app in "${ok[@]}"; do echo "- ✅ $app"; done
	for app in "${failed[@]}"; do echo "- ❌ $app (see the 'build-failure' issue)"; done
} >>"${GITHUB_STEP_SUMMARY:-/dev/null}"

echo "built=${#ok[@]}" >>"${GITHUB_OUTPUT:-/dev/null}"
[ ${#failed[@]} -eq 0 ]
