# Source this to put the Forge workspace packages on PYTHONPATH.
#   source scripts/env.sh
#
# Each package is a real distribution (its own pyproject.toml) so CI and production
# use `pip install .`. For local development we set PYTHONPATH explicitly rather than
# using `pip install -e`, because this project is developed on hosts whose interpreter
# does not process .pth path-configuration files, which makes editable installs
# silently inert. PYTHONPATH is explicit, portable and inherited by subprocesses.
FORGE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")/.." && pwd)"
export FORGE_ROOT
export PYTHONPATH="\
$FORGE_ROOT/packages/forge-core:\
$FORGE_ROOT/apps/api:\
$FORGE_ROOT/apps/worker:\
$FORGE_ROOT/apps/router:\
$FORGE_ROOT/apps/sample-service${PYTHONPATH:+:$PYTHONPATH}"
export FORGE_PG_DATA_DIR="${FORGE_PG_DATA_DIR:-$FORGE_ROOT/.forge/pgdata}"
