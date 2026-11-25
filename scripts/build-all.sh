#!/usr/bin/env bash
# Build every app given as arguments.
#
# 1. Build with the pinned engine (engine.lock). Each good build is released
#    right away and the store update is started, so apps appear one by one.
# 2. Apps that failed get one more try with the newest upstream engine.
#    If that works, the pin moves forward by itself (upstream fixed it).
# 3. Failures open or update a GitHub issue.
set -uo pipefail
cd "$(dirname "$0")/.."

APPS=("$@")
OST="python3 scripts/ostore.py"
ok=() failed=()

publish() { # app -> release, remember, refresh store, close old failure issue
	local app=$1
	if $OST release "$app"; then
		$OST record "$app"
		# Store updates queue, so starting one per app never collides.
		gh workflow run pages.yml --ref main >/dev/null 2>&1 || echo "::warning::could not start the store update"
		$OST issue-ok "$app" || true
		ok+=("$app")
	else
		mkdir -p "out/$app"
		[ -f "out/$app/error.json" ] || echo '{"step":"release","message":"Uploading the GitHub release failed"}' >"out/$app/error.json"
		failed+=("$app")
	fi
}

PIN=$($OST engine --dir engine | sed -n 's/^sha=//p')
[ -n "$PIN" ] || { echo "::error::could not fetch the pinned engine"; $OST issue-fail engine --step fetch-engine --message "Could not download the pinned j-hc engine."; exit 1; }

retry=()
for app in "${APPS[@]}"; do
	echo "::group::Build $app (pinned engine ${PIN:0:10})"
	if $OST build "$app" --engine engine --engine-sha "$PIN"; then publish "$app"; else retry+=("$app"); fi
	echo "::endgroup::"
done

if [ ${#retry[@]} -gt 0 ]; then
	HEAD_SHA=$($OST engine --dir engine-head --sha HEAD | sed -n 's/^sha=//p')
	if [ -n "$HEAD_SHA" ] && [ "$HEAD_SHA" != "$PIN" ]; then
		bumped=false
		for app in "${retry[@]}"; do
			echo "::group::Retry $app with newest upstream engine ${HEAD_SHA:0:10}"
			if $OST build "$app" --engine engine-head --engine-sha "$HEAD_SHA"; then
				bumped=true
				publish "$app"
			else
				failed+=("$app")
			fi
			echo "::endgroup::"
		done
		if [ "$bumped" = true ]; then
			sed -i "s/^JHC_COMMIT=.*/JHC_COMMIT=${HEAD_SHA}/" engine.lock
			echo "Engine pin moved to ${HEAD_SHA} because it fixed a failing build."
		fi
	else
		echo "Newest upstream engine is the same as the pin; no retry."
		failed+=("${retry[@]}")
	fi
fi

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
