"""BUG-001 managed guidance regressions; static checks never touch runtime state."""

from pathlib import Path
import re
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[2]
SKILLS = sorted((ROOT / "skills").glob("*/SKILL.md"))
OVERLAYS = sorted((ROOT / "templates/e2e").glob(".*/skills/*/SKILL.md.tmpl"))
INSTRUCTIONS = [
    ROOT / "templates/e2e/AGENTS.md.tmpl",
    ROOT / "templates/e2e/.github/copilot-instructions.md.tmpl",
]
README = ROOT / "README.md"


def normalized(path):
    return " ".join(path.read_text().split()).replace("`", "")


class ManagedGuidanceTests(unittest.TestCase):
    def test_all_canonical_skills_keep_durable_contract_pointer(self):
        self.assertEqual(len(SKILLS), 8)
        for path in SKILLS:
            with self.subTest(path=path.relative_to(ROOT)):
                text = normalized(path)
                for required in (
                    "accept-dispatch", "heartbeat", "pending", "mission-status",
                    "command-status", "--result", "digest", "human",
                ):
                    self.assertIn(required, text, msg=f"{path}: missing {required}")

    def test_both_overlay_trees_and_instructions_keep_receipt_dialog_ack_contract(self):
        self.assertEqual(len(OVERLAYS), 12)
        for path in OVERLAYS + INSTRUCTIONS:
            with self.subTest(path=path.relative_to(ROOT)):
                text = normalized(path)
                for required in (
                    "accept-dispatch", "start_work=true", "heartbeat",
                    "access-prompt", "pending", "read-question", "mission-status",
                    "hold --answers", "command-status", "pending_commands",
                    "accepted then applied", "--result", "digest", "human",
                    "pane diagnosis",
                ):
                    self.assertIn(required, text)

    def test_no_obsolete_commands_or_shared_question_files(self):
        obsolete = (
            r"/tmp/\S*(?:question|answer)",
            r"--(?:blocked-on|question|answer|options)\b",
            r"docs/queue\b", r"\.cockpit/control\b", r"e2e/tools/cockpit-wake",
            r"cockpit-protocol send[^\n]*(?:git status|/clear|STOP)",
            r"cockpit-protocol command-status[^\n]*--json",
            r"fire messages into", r"messages into any",
        )
        for path in [README] + SKILLS + OVERLAYS + INSTRUCTIONS:
            with self.subTest(path=path.relative_to(ROOT)):
                text = path.read_text()
                for pattern in obsolete:
                    self.assertNotRegex(text, pattern)

    def test_bootstrap_and_recovery_walkthrough_has_required_boundaries(self):
        text = normalized(README)
        for required in (
            "cockpit-control init", "cockpit-control bind-roots",
            "--queue-root", "--planning-root", "--implementation-root",
            "docs/cockpit-control", "docs/cockpit-queue", "operationally-blocked",
            "recover-dispatch --dispatch-command", "--inspect-safe", "--by operator",
            "--evidence", "fences late receipts", "project-owned",
            "dispatch-acceptance-expired", "dispatch-acceptance-unsupported",
        ):
            self.assertIn(required, text)

    def test_scheduler_and_setup_guidance_preserve_current_adoption_contract(self):
        for path in (README, ROOT / "skills/setup-e2e-cockpit/SKILL.md",
                     ROOT / "skills/e2e-cockpit/SKILL.md"):
            with self.subTest(path=path.relative_to(ROOT)):
                text = normalized(path)
                for required in (
                    "--mission", "--queue-item", "--owner", "--intent",
                    "--stop-condition", "--dry-run", "read-only", "reschedule",
                    "fired recurring", "diagnos", "bind-roots",
                ):
                    self.assertIn(required, text)
        setup = normalized(ROOT / "skills/setup-e2e-cockpit/SKILL.md")
        self.assertIn("bootstrap.sh global", setup)
        self.assertIn("bootstrap.sh codex-global", setup)
        self.assertIn("modules", setup)

    def test_repo_codex_exposures_remain_canonical_symlinks(self):
        for canonical in SKILLS:
            exposed = ROOT / ".agents/skills" / canonical.parent.name / "SKILL.md"
            with self.subTest(role=canonical.parent.name):
                self.assertTrue(exposed.is_symlink())
                self.assertEqual(exposed.resolve(), canonical)

    def test_readme_bash_examples_parse_without_execution(self):
        examples = re.findall(r"```bash\n(.*?)```", README.read_text(), re.S)
        self.assertGreater(len(examples), 10)
        for index, example in enumerate(examples):
            with self.subTest(example=index):
                result = subprocess.run(
                    ["bash", "-n"], input=example, text=True, capture_output=True,
                )
                self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
