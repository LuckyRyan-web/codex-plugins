import json
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
MARKETPLACE_PATH = ROOT / ".agents" / "plugins" / "marketplace.json"
PLUGIN_ROOT = ROOT / "plugins" / "task-completion-guard"
MANIFEST_PATH = PLUGIN_ROOT / ".codex-plugin" / "plugin.json"
HOOKS_PATH = PLUGIN_ROOT / "hooks" / "hooks.json"


def load_json(path):
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


class ReleasePackageTests(unittest.TestCase):
    def test_marketplace_points_to_packaged_plugin(self):
        marketplace = load_json(MARKETPLACE_PATH)
        self.assertEqual(marketplace["name"], "luckyryan-codex-plugins")
        self.assertEqual(marketplace["interface"]["displayName"], "LuckyRyan Codex Plugins")
        self.assertEqual(len(marketplace["plugins"]), 1)

        entry = marketplace["plugins"][0]
        self.assertEqual(entry["name"], "task-completion-guard")
        self.assertEqual(entry["source"]["source"], "local")
        self.assertEqual(entry["policy"]["installation"], "AVAILABLE")
        self.assertEqual(entry["policy"]["authentication"], "ON_INSTALL")
        self.assertEqual(entry["category"], "Productivity")

        relative_source = entry["source"]["path"]
        self.assertTrue(relative_source.startswith("./"))
        resolved_source = (ROOT / relative_source).resolve()
        resolved_source.relative_to(ROOT.resolve())
        self.assertEqual(resolved_source, PLUGIN_ROOT.resolve())
        self.assertTrue(MANIFEST_PATH.is_file())

    def test_plugin_manifest_has_public_release_metadata(self):
        manifest = load_json(MANIFEST_PATH)
        self.assertEqual(manifest["name"], PLUGIN_ROOT.name)
        self.assertRegex(
            manifest["version"],
            r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
            r"(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$",
        )
        self.assertTrue(manifest["description"])
        self.assertEqual(manifest["author"]["name"], "LuckyRyan-web")
        self.assertEqual(manifest["repository"], "https://github.com/LuckyRyan-web/codex-plugins")
        self.assertEqual(manifest["license"], "MIT")
        self.assertEqual(manifest["skills"], "./skills/")
        self.assertEqual(manifest["interface"]["displayName"], "Task Completion Guard")

    def test_default_plugin_components_exist(self):
        self.assertTrue(HOOKS_PATH.is_file())
        self.assertTrue((PLUGIN_ROOT / "scripts" / "completion_guard.py").is_file())
        self.assertTrue(
            (PLUGIN_ROOT / "skills" / "task-completion-guard" / "SKILL.md").is_file()
        )

        hooks = load_json(HOOKS_PATH)
        self.assertEqual(
            set(hooks["hooks"]),
            {"UserPromptSubmit", "PostToolUse", "Stop", "Interrupt"},
        )
        serialized = json.dumps(hooks)
        self.assertIn("${PLUGIN_ROOT}/scripts/completion_guard.py", serialized)
        self.assertNotRegex(serialized, r"/Users/|[A-Za-z]:\\Users\\")

    def test_public_files_do_not_contain_local_absolute_paths(self):
        forbidden = re.compile(
            re.escape("/Users/" + "liuyuan")
            + "|"
            + re.escape("/private/tmp/" + "codex-plugins-release")
        )
        text_extensions = {".json", ".md", ".py", ".yaml", ".yml"}
        for path in ROOT.rglob("*"):
            if not path.is_file() or ".git" in path.parts:
                continue
            if path.suffix.lower() not in text_extensions and path.name != "LICENSE":
                continue
            content = path.read_text(encoding="utf-8")
            self.assertIsNone(forbidden.search(content), str(path.relative_to(ROOT)))

    def test_readme_contains_complete_install_and_trust_flow(self):
        expected_commands = [
            "codex plugin marketplace add LuckyRyan-web/codex-plugins --ref main",
            "codex plugin add task-completion-guard@luckyryan-codex-plugins",
            "codex plugin marketplace upgrade luckyryan-codex-plugins",
            "codex plugin remove task-completion-guard@luckyryan-codex-plugins",
            "codex plugin marketplace remove luckyryan-codex-plugins",
        ]
        for readme_name in ("README.md", "README.zh-CN.md"):
            readme = (ROOT / readme_name).read_text(encoding="utf-8")
            for command in expected_commands:
                self.assertIn(command, readme, readme_name)
            self.assertIn("/hooks", readme, readme_name)
            self.assertIn("macOS", readme, readme_name)
            self.assertIn("Linux", readme, readme_name)
            self.assertIn("Windows", readme, readme_name)
            self.assertIn("Codex CLI", readme, readme_name)


if __name__ == "__main__":
    unittest.main()
