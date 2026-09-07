import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BIN = ROOT / "bin"
if str(BIN) not in sys.path:
    sys.path.insert(0, str(BIN))

import cockpit_control as cc
import cockpit_control_cli as control_cli
import cockpit_control_queue_adapter as queue_adapter
import cockpit_control_rendering as rendering_adapter
import cockpit_control_tmux_adapter as tmux_adapter


def _run(args, env=None, cwd=None):
    return subprocess.run(args, check=False, capture_output=True, text=True, env=env, cwd=cwd)


def _snapshot(root: Path):
    rows = []
    for current, directories, files in os.walk(root):
        directories.sort()
        files.sort()
        current_path = Path(current)
        for name in directories:
            rows.append(("dir", str((current_path / name).relative_to(root))))
        for name in files:
            path = current_path / name
            rows.append(("file", str(path.relative_to(root)), path.read_bytes()))
    return tuple(rows)


class ControlCliAdapterSeamTests(unittest.TestCase):
    def test_import_compatibility_through_facade(self):
        self.assertIs(cc.main, control_cli.main)
        self.assertIs(cc.observe_queue, queue_adapter.observe_queue)
        self.assertIs(cc._print_error, rendering_adapter._print_error)
        self.assertIs(cc._session_identity, tmux_adapter.session_identity)

    def test_wrapper_help_and_init_validate_output_contract_stays_stable(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            control_root = tmp_path / "control"
            env = dict(os.environ)
            env["COCKPIT_CONTROL_ROOT"] = str(control_root)
            env.pop("PYTHONPATH", None)

            help_result = _run([str(BIN / "cockpit-control"), "--help"], env=env, cwd=tmp)
            self.assertEqual(help_result.returncode, 0)
            self.assertIn("record-lifecycle", help_result.stdout)
            self.assertIn("repair-store", help_result.stdout)

            init = _run([str(BIN / "cockpit-control"), "init"], env=env)
            self.assertEqual(init.returncode, 0, init.stderr)
            self.assertIn(f"cockpit-control: initialized {control_root}", init.stdout)
            self.assertIn("(source: ", init.stdout)

            validate = _run([str(BIN / "cockpit-control"), "validate"], env=env)
            self.assertEqual(validate.returncode, 0, validate.stderr)
            self.assertIn(f"cockpit-control: valid {control_root}", validate.stdout)
            self.assertIn("(source: ", validate.stdout)

    def test_queue_adapter_error_translation_and_dry_run_no_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            control_root = Path(tmp) / "control"
            env = dict(os.environ)
            env["COCKPIT_CONTROL_ROOT"] = str(control_root)

            init = _run([str(BIN / "cockpit-control"), "init"], env=env)
            self.assertEqual(init.returncode, 0, init.stderr)
            metadata = json.loads((control_root / cc.CONTROL_METADATA_NAME).read_text())
            self.assertEqual(metadata["schema_version"], cc.CONTROL_SCHEMA_VERSION)

            before = _snapshot(control_root)
            dry = _run([str(BIN / "cockpit-control"), "repair-store", "--dry-run"], env=env)
            after = _snapshot(control_root)
            self.assertEqual(dry.returncode, 0, dry.stderr)
            self.assertEqual(before, after)
            self.assertIn("no state changed", dry.stdout)

            with self.assertRaises(cc.ControlStoreError) as exc:
                queue_adapter.observe_queue(Path("relative/queue"))
            self.assertIn("declared COCKPIT_QUEUE_ROOT must be an absolute path", str(exc.exception))


if __name__ == "__main__":
    unittest.main()
