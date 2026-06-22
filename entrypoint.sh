#!/bin/bash
set -euo pipefail

if [[ -n "${INPUT_POETRY_VERSION:-}" ]]; then
	if [[ ! "${INPUT_POETRY_VERSION}" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
		echo "Invalid Poetry version: ${INPUT_POETRY_VERSION}. Expected format X.Y.Z." >&2
		exit 1
	fi
	echo "Installing Poetry ${INPUT_POETRY_VERSION} into isolated venv"
	VENV_DIR=$(mktemp -d /tmp/poetry-venv.XXXX)
	python3 -m venv "$VENV_DIR"
	"$VENV_DIR/bin/pip" install --no-cache-dir "poetry==${INPUT_POETRY_VERSION}"
	POETRY_SITE_PACKAGES=$(
		"$VENV_DIR/bin/python" -c 'import site; print(site.getsitepackages()[0])'
	)
	export PYTHONPATH="${POETRY_SITE_PACKAGES}${PYTHONPATH:+:${PYTHONPATH}}"
	python3 - <<'PY'
import importlib.util
from importlib.metadata import version

spec = importlib.util.find_spec("poetry")
location = (spec.origin if spec else None) or (
	list(spec.submodule_search_locations)[0]
	if spec and spec.submodule_search_locations
	else "unknown"
)

print("Using Poetry {} from {}".format(version("poetry"), location))
PY
fi

exec python3 -m diff_poetry_lock.run_poetry
