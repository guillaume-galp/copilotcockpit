import sys
import unittest
from pathlib import Path
import tempfile

# Ensure bin on sys.path so we import the facade and seams as installed scripts
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / 'bin') not in sys.path:
    sys.path.insert(0, str(ROOT / 'bin'))

import cockpit_control as cc  # the compatibility facade (unchanged runtime)
import cockpit_control_seams as seams
import cockpit_control_commands as control_commands

class TypedContractConversionTests(unittest.TestCase):
    def test_facade_re_exports_command_digest_symbol(self):
        self.assertIs(cc.command_payload_digest, control_commands.command_payload_digest)
        self.assertEqual(cc.COMMAND_SCHEMA_VERSION, control_commands.COMMAND_SCHEMA_VERSION)
        self.assertEqual(
            cc.COMMAND_ENVELOPE_RECORD_TYPE,
            control_commands.COMMAND_ENVELOPE_RECORD_TYPE,
        )

    def test_command_envelope_roundtrip_preserves_wire_shape(self):
        payload = {"field": "value", "z": [1,2,3]}
        digest = control_commands.command_payload_digest(payload)
        envelope = {
            "schema_version": control_commands.COMMAND_SCHEMA_VERSION,
            "record_type": control_commands.COMMAND_ENVELOPE_RECORD_TYPE,
            "command_id": "11111111-1111-1111-1111-111111111111",
            "command_type": "example-type",
            "mission_id": "22222222-2222-2222-2222-222222222222",
            "queue_item_id": "QI-1",
            "target": {"kind": control_commands.COMMAND_TARGET_WORKER, "id": "worker-dev"},
            "trace_id": "33333333-3333-3333-3333-333333333333",
            "parent_trace_id": None,
            "payload_digest": digest,
            "boundaries": {
                "control_root": "/tmp/control",
                "queue_root": None,
                "planning_root": None,
                "implementation_roots": [],
                "runtime_boundaries": [],
            },
            "created_at": cc.utc_timestamp(),
            "deadline_at": None,
        }
        typed = seams.envelope_from_dict(envelope)
        back = seams.envelope_to_dict(typed)
        self.assertEqual(envelope, back)

    def test_command_payload_canonicalization_keeps_digest(self):
        payload = {"alpha": 1, "beta": ["x", "y"]}
        digest1 = control_commands.command_payload_digest(payload)
        digest2 = control_commands.command_payload_digest(payload)
        self.assertEqual(digest1, digest2)

class DependencyDirectionTests(unittest.TestCase):
    def test_check_finds_forbidden_import_in_domain(self):
        tdir = tempfile.mkdtemp(prefix='cc-test-')
        domain_dir = Path(tdir) / 'domain'
        domain_dir.mkdir()
        # Negative case: import that should NOT be detected (substring only)
        f_neg = domain_dir / 'other_module.py'
        f_neg.write_text('import acliquer\n')

        # When the negative-only file is present, the checker should not raise
        seams.check_dependency_direction([domain_dir], banned_adapter_names=("tmux", "cli"), detect_cycles=False)

        # Positive case: import that should be detected (segment match)
        f_pos = domain_dir / 'seam_module.py'
        f_pos.write_text('import tmux_adapter\n')

        # When the positive file is present, the checker must raise
        raised = False
        try:
            seams.check_dependency_direction([domain_dir], banned_adapter_names=("tmux", "cli"), detect_cycles=False)
        except Exception:
            raised = True
        self.assertTrue(raised, 'expected DependencyError to be raised for tmux_adapter import')

    def test_banned_adapter_matching_realistic_cases(self):
        tdir = tempfile.mkdtemp(prefix='cc-test-')
        domain_dir = Path(tdir) / 'domain2'
        domain_dir.mkdir()
        # positive examples
        (domain_dir / 'a.py').write_text('import tmux_adapter\n')
        (domain_dir / 'b.py').write_text('import rendering_engine\n')
        (domain_dir / 'c.py').write_text('import queue_item\n')
        # negative examples
        (domain_dir / 'd.py').write_text('import sequel\n')
        (domain_dir / 'e.py').write_text('import acliquer\n')
        (domain_dir / 'f.py').write_text('import prendering\n')

        # Calculate violations using the same segmentation logic the checker uses
        violations = []
        for p in sorted(domain_dir.iterdir()):
            imports = seams._extract_imported_names(p)
            for name in imports:
                for token in ("cli", "rendering", "tmux", "queue"):
                    if seams._name_matches_token(name, token):
                        violations.append((p.name, name))

        # Ensure positives are present and negatives are not reported
        reported = {n for (_, n) in violations}
        self.assertIn('tmux_adapter', reported)
        self.assertIn('rendering_engine', reported)
        self.assertIn('queue_item', reported)
        self.assertNotIn('sequel', reported)
        self.assertNotIn('acliquer', reported)
        self.assertNotIn('prendering', reported)

if __name__ == '__main__':
    unittest.main()
