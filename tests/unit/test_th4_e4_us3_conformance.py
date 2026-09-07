import ast
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BIN = ROOT / "bin"
if str(BIN) not in sys.path:
    sys.path.insert(0, str(BIN))


MANAGED_MODULES_FILE = ROOT / "MANAGED_RUNTIME_MODULES"
US3_CONFORMANCE_DOC = (
    ROOT
    / "docs"
    / "themes"
    / "TH4-control-plane-modularity-ontology"
    / "epics"
    / "E4-cli-conformance-cleanup"
    / "TH4-E4-US3-conformance.md"
)


class TH4E4US3ConformanceTests(unittest.TestCase):
    def _managed_modules(self):
        modules = []
        for line in MANAGED_MODULES_FILE.read_text(encoding="utf-8").splitlines():
            candidate = line.strip()
            if not candidate or candidate.startswith("#"):
                continue
            modules.append(candidate)
        return tuple(modules)

    def _module_imports(self, module_name):
        path = BIN / f"{module_name}.py"
        self.assertTrue(path.exists(), f"managed module source missing: {path}")
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imported.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        return imported

    def test_managed_runtime_modules_are_importable(self):
        for module_name in self._managed_modules():
            __import__(module_name)

    def test_dependency_direction_is_acyclic_and_layered(self):
        managed = set(self._managed_modules())
        graph = {name: set() for name in managed}
        for name in managed:
            imports = self._module_imports(name)
            graph[name] = {dep for dep in imports if dep in managed and dep != name}

        layer = {
            "cockpit_control_root_schema": 0,
            "cockpit_control_locks": 1,
            "cockpit_control_journal": 1,
            "cockpit_control_projection": 1,
            "cockpit_control_lifecycle": 1,
            "cockpit_control_commands": 1,
            "cockpit_control_mission_control": 2,
            "cockpit_control_controller": 2,
            "cockpit_control_wake": 2,
            "cockpit_control_queue_adapter": 3,
            "cockpit_control_tmux_adapter": 3,
            "cockpit_control_rendering": 3,
            "cockpit_control_cli": 3,
            "cockpit_control": 4,
        }
        self.assertEqual(set(layer.keys()), managed)

        violations = []
        for src, deps in graph.items():
            if src == "cockpit_control":
                continue
            for dep in deps:
                if layer[dep] > layer[src]:
                    violations.append((src, dep))
        self.assertEqual([], violations, f"dependency direction violations: {violations}")

        visiting = set()
        visited = set()

        def visit(node):
            if node in visiting:
                return True
            if node in visited:
                return False
            visiting.add(node)
            for dep in graph[node]:
                if visit(dep):
                    return True
            visiting.remove(node)
            visited.add(node)
            return False

        self.assertFalse(any(visit(node) for node in sorted(graph.keys())), f"import cycle found: {graph}")

    def test_wrappers_keep_facade_import_boundary(self):
        cockpit_control_wrapper = (BIN / "cockpit-control").read_text(encoding="utf-8")
        cockpit_overseer_wrapper = (BIN / "cockpit-overseer").read_text(encoding="utf-8")
        cockpit_wake_wrapper = (BIN / "cockpit-wake").read_text(encoding="utf-8")

        self.assertIn("from cockpit_control import main", cockpit_control_wrapper)
        self.assertIn("from cockpit_control import (", cockpit_overseer_wrapper)
        self.assertIn("from cockpit_control import (", cockpit_wake_wrapper)

    def test_cli_help_contract_stays_stable_for_th4_boundary(self):
        result = subprocess.run(
            [str(BIN / "cockpit-control"), "--help"],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        for expected in (
            "record-lifecycle",
            "acknowledge-command",
            "mission-status",
            "replay-ledger",
            "repair-store",
        ):
            self.assertIn(expected, result.stdout)

    def test_vocabulary_alias_and_rollback_guidance_are_documented(self):
        text = US3_CONFORMANCE_DOC.read_text(encoding="utf-8")
        self.assertIn("Legacy compatibility aliases accepted during TH4", text)
        self.assertIn("Rollback guidance", text)
        self.assertIn("`cockpit_control` facade", text)
        self.assertIn("No control-store data migration", text)


if __name__ == "__main__":
    unittest.main()
