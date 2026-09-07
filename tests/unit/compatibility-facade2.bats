#!/usr/bin/env bats
# Focused compatibility-facade tests for TH4.E1.US1 (alternative file)

load helper

setup() {
    cc_setup_fake_home
    # absolute path to repository bin to ensure wrappers execute from any CWD
    export REPO_BIN="$(cd "$BATS_TEST_DIRNAME/../../bin" && pwd -P)"
    unset COCKPIT_CONTROL_ROOT TMUX TMUX_SESSION COCKPIT_SESSION_ID COCKPIT_ID
}

@test "import cockpit_control from the facade has no import-time side-effects" {
    pyfile="$BATS_TEST_TMPDIR/import_test.py"
    cat > "$pyfile" <<'PY'
import sys, os, importlib
sys.path.insert(0, sys.argv[1])
# Record current working directory files
cwd_files_before = set(os.listdir('.'))
m = importlib.import_module('cockpit_control')
assert hasattr(m, 'CONTROL_SCHEMA_VERSION')
assert hasattr(m, 'resolve_control_root')
# ensure import didn't create files in cwd
cwd_files_after = set(os.listdir('.'))
assert cwd_files_before == cwd_files_after
print('ok')
PY

    run python3 "$pyfile" "$REPO_BIN"
    [ "$status" -eq 0 ]
    echo "$output" | grep -q "ok"
}

@test "wrapper entrypoint resolves cockpit_control when run from outside source" {
    tmpdir="$BATS_TEST_TMPDIR/outside-cwd"
    mkdir -p "$tmpdir"
    # Run wrapper from outside repository with PYTHONPATH unset to ensure isolation
    run env -u PYTHONPATH bash -c 'cd "$1" && exec "$2" --help' -- "$tmpdir" "$REPO_BIN/cockpit-control"
    # argparse may exit with 0 or 2 when printing help/usage; ensure no traceback
    [[ "$status" -eq 0 || "$status" -eq 2 ]]
    # argparse may line-wrap option lists; require stable tokens rather than exact contiguous substring
    echo "$output" | grep -q "initialize"
    echo "$output" | grep -q "validate"
}

@test "installed wrapper on PATH (if present) runs isolated and reports help" {
    if ! command -v cockpit-control >/dev/null 2>&1; then
        skip "no globally installed cockpit-control on PATH to test"
    fi
    tmpdir="$BATS_TEST_TMPDIR/outside-installed"
    mkdir -p "$tmpdir"
    run env -u PYTHONPATH bash -c 'cd "$1" && exec cockpit-control --help' -- "$tmpdir"
    [[ "$status" -eq 0 || "$status" -eq 2 ]]
    echo "$output" | grep -q "initialize"
    echo "$output" | grep -q "validate"
}

@test "MANAGED_RUNTIME_MODULES declares facade and extracted control-root module" {
    run cat "$BATS_TEST_DIRNAME/../../MANAGED_RUNTIME_MODULES"
    [ "$status" -eq 0 ]
    echo "$output" | grep -q "cockpit_control"
    echo "$output" | grep -q "cockpit_control_root_schema"
}
