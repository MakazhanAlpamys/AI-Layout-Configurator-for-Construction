"""Installed wheels work without repository-relative schemas, rules or IDS."""

import json
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
import zipfile


PROJECT = Path(__file__).resolve().parents[1]
RESOURCE_PATTERNS = {"schemas": "*.json", "rules": "*.yaml", "ids": "*.ids"}


class InstalledWheelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        cls.root = Path(cls.temp.name)
        cls.source = cls.root / "source"
        cls.source.mkdir()
        # Build in a disposable source tree so stale egg-info/build output cannot
        # accidentally provide resources missing from the distribution contract.
        for name in ("pyproject.toml", "README.md", "setup.py", "MANIFEST.in"):
            if (PROJECT / name).is_file():
                shutil.copy2(PROJECT / name, cls.source / name)
        shutil.copytree(PROJECT / "src" / "layout_configurator",
                        cls.source / "src" / "layout_configurator",
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        for directory in RESOURCE_PATTERNS:
            shutil.copytree(PROJECT / directory, cls.source / directory)
        cls.wheels = cls.root / "wheels"
        cls._command([sys.executable, "-m", "pip", "wheel", "--no-deps", "--no-build-isolation",
                      "--wheel-dir", str(cls.wheels), str(cls.source)])
        cls.wheel = next(cls.wheels.glob("*.whl"))
        cls.installed = cls.root / "installed"
        cls._command([sys.executable, "-m", "pip", "install", "--no-deps", "--target",
                      str(cls.installed), str(cls.wheel)])
        cls.work = cls.root / "outside-checkout"
        cls.work.mkdir()
        canonical = json.loads((PROJECT / "examples" / "basic_canonical.json").read_text(encoding="utf-8"))
        (cls.work / "basic.json").write_text(json.dumps(canonical), encoding="utf-8")
        (cls.work / "building.json").write_text(json.dumps({
            "version": "0.2", "project_name": canonical["project_name"], "layout": canonical,
            "zones": [], "equipment": [], "flows": [],
        }), encoding="utf-8")

    @classmethod
    def _command(cls, command):
        result = subprocess.run(command, cwd=cls.root, text=True, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, timeout=120)
        if result.returncode:
            raise AssertionError(f"Distribution command failed: {command}\n{result.stdout}")
        return result

    def _run(self, code, *arguments, cwd=None):
        # Keep the real installed dependencies, but import every project module
        # from the non-editable wheel. -I excludes cwd/PYTHONPATH shortcuts.
        prefix = f"import sys; from pathlib import Path; sys.path.insert(0, {str(self.installed)!r});\n"
        guard = (
            "\nassert all(Path(module.__file__).resolve().is_relative_to(Path(sys.path[0]).resolve()) "
            "for name, module in tuple(sys.modules.items()) "
            "if name.startswith('layout_configurator') and getattr(module, '__file__', None))\n"
        )
        result = subprocess.run([sys.executable, "-I", "-c", prefix + code + guard, *map(str, arguments)],
                                cwd=cwd or self.work, text=True, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, timeout=90)
        self.assertEqual(result.returncode, 0, result.stdout)
        return result.stdout

    def _cli(self, *arguments):
        output = self._run(
            "from layout_configurator.cli import main\n"
            "exit_code = main(sys.argv[1:])\n"
            "assert exit_code == 0, f'CLI failed with exit {exit_code}'\n", *arguments)
        return output

    def test_wheel_contains_the_authoritative_schema_rule_and_ids_bytes(self):
        # An omitted or stale bundled file breaks a fresh install even if all
        # development tests pass using the source checkout.
        with zipfile.ZipFile(self.wheel) as archive:
            for directory, pattern in RESOURCE_PATTERNS.items():
                for source in sorted((PROJECT / directory).glob(pattern)):
                    member = f"layout_configurator/_data/{directory}/{source.name}"
                    with self.subTest(resource=member):
                        self.assertTrue(member in archive.namelist(), f"Missing wheel resource: {member}")
                        self.assertEqual(archive.read(member), source.read_bytes())

    def test_sdist_can_rebuild_a_wheel_with_the_same_bundled_resources(self):
        # A local wheel alone can hide missing source assets in a published sdist.
        output = self.root / "sdist"
        output.mkdir()
        result = subprocess.run(
            [sys.executable, "-c", "from setuptools.build_meta import build_sdist; "
             f"build_sdist({str(output)!r})"], cwd=self.source, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=120)
        self.assertEqual(result.returncode, 0, result.stdout)
        archive_path = next(output.glob("*.tar.gz"))
        extracted = self.root / "sdist-source"
        with tarfile.open(archive_path) as archive:
            names = archive.getnames()
            prefix = names[0].split("/")[0]
            for directory, pattern in RESOURCE_PATTERNS.items():
                for source in sorted((PROJECT / directory).glob(pattern)):
                    member = f"{prefix}/{directory}/{source.name}"
                    self.assertTrue(member in names, f"Missing sdist resource: {member}")
            for source in sorted((PROJECT / "src/layout_configurator/fonts").glob("*")):
                member = f"{prefix}/src/layout_configurator/fonts/{source.name}"
                self.assertIn(member, names, f"Missing sdist font resource: {member}")
            archive.extractall(extracted, filter="data")
        wheels = self.root / "sdist-wheels"
        self._command([sys.executable, "-m", "pip", "wheel", "--no-deps", "--no-build-isolation",
                       "--wheel-dir", str(wheels), str(extracted / prefix)])
        with zipfile.ZipFile(next(wheels.glob("*.whl"))) as archive:
            for directory, pattern in RESOURCE_PATTERNS.items():
                for source in sorted((PROJECT / directory).glob(pattern)):
                    member = f"layout_configurator/_data/{directory}/{source.name}"
                    self.assertEqual(archive.read(member), source.read_bytes())
            for source in sorted((PROJECT / "src/layout_configurator/fonts").glob("*")):
                self.assertEqual(archive.read(f"layout_configurator/fonts/{source.name}"), source.read_bytes())

    def test_installed_loaders_find_defaults_from_a_foreign_working_directory(self):
        # Hard-coded parents[2] paths, missing package resources and unbundled
        # inheritance bases all fail at the installed consumer boundary.
        output = self._run(
            "import json\n"
            "from layout_configurator.schema import normalize_mapping, normalize_building_mapping\n"
            "from layout_configurator.facility import default_facility_profile, load_facility_profile\n"
            "from layout_configurator.norms import load_ruleset\n"
            "from layout_configurator.issue_management import new_issue_management_state, write_issue_management, load_issue_management\n"
            "from layout_configurator.api import _runtime_fingerprint\n"
            "layout = normalize_mapping(json.loads(Path('basic.json').read_text()))\n"
            "building = normalize_building_mapping(json.loads(Path('building.json').read_text()))\n"
            "profile = default_facility_profile()\n"
            "pharma = load_facility_profile('rules/pharma_cleanroom_pilot.yaml')\n"
            "rules = load_ruleset('rules/kz_sn_3_02_02_2023_partial.yaml')\n"
            "write_issue_management('issues.json', new_issue_management_state())\n"
            "assert load_issue_management('issues.json')['issues'] == []\n"
            "assert len(_runtime_fingerprint()) == 64\n"
            "print(json.dumps({'rooms': len(layout.rooms), 'building_rooms': len(building.layout.rooms),\n"
            "                  'default_domain': profile.domain, 'pharma_domain': pharma.domain,\n"
            "                  'jurisdiction': rules.jurisdiction, 'rules': len(rules.rules)}))\n"
        )
        report = json.loads(output)
        self.assertEqual(report["rooms"], 4)
        self.assertEqual(report["building_rooms"], 4)
        self.assertEqual(report["default_domain"], "generic-facility")
        self.assertEqual(report["pharma_domain"], "pharma-cleanroom")
        self.assertEqual(report["jurisdiction"], "KZ")
        self.assertGreater(report["rules"], 0)

    def test_installed_cli_generates_and_validates_using_bundled_defaults(self):
        # CLI defaults are relative strings today: exercise the actual argument
        # parser/export/check/IDS/bundle-QA paths outside the checkout.
        self._cli("generate", "basic.json", "--strict-input", "--output", "layout-output",
                  "--variants", "1", "--time-limit", "5")
        self._cli("validate", "layout-output/layout_01.ifc")
        self._cli("check", "layout-output/layout_01.json", "--json")
        self._cli("generate-building", "building.json", "--output", "facility-output",
                  "--variants", "1", "--time-limit", "5")
        report = json.loads(self._cli("qa-building", "facility-output/building_01.json", "--json"))
        self.assertTrue(report["ok"], report)
        self.assertEqual({check["id"] for check in report["checks"]},
                         {"FACILITY_VALIDATION", "MANIFEST", "DXF_READBACK", "PDF_READBACK",
                          "IFC_READBACK", "BCF_READBACK", "COORDINATION_JSON"})

    def test_installed_proposed_edits_validate_with_the_bundled_schema(self):
        self._run(
            "import json\n"
            "from layout_configurator.schema import normalize_building_mapping\n"
            "from layout_configurator.proposed_edits import propose_edits, write_proposed_edits, read_proposed_edits\n"
            "building = normalize_building_mapping(json.loads(Path('building.json').read_text()))\n"
            "draft = propose_edits(building, {'edits': [{'operation': 'set_room', 'target_id': 'hall',\n"
            "       'changes': {'rotatable': False}}]}, source_text='Keep hall orientation fixed')\n"
            "write_proposed_edits('draft.json', draft)\n"
            "assert read_proposed_edits('draft.json').to_dict() == draft.to_dict()\n"
        )

    def test_installed_cli_uses_pinned_licensed_fonts_for_cyrillic_sheets(self):
        from test_drawing_fonts import cyrillic_program
        source = self.work / "cyrillic.json"
        source.write_text(json.dumps(cyrillic_program(), ensure_ascii=False), encoding="utf-8")
        with zipfile.ZipFile(self.wheel) as archive:
            for name in ("NotoSans-Regular.ttf", "NotoSans-Bold.ttf", "OFL.txt", "SOURCE.json"):
                member = f"layout_configurator/fonts/{name}"
                self.assertIn(member, archive.namelist(), f"Missing portable licensed font resource: {name}")
                self.assertEqual(archive.read(member), (PROJECT / "src/layout_configurator/fonts" / name).read_bytes())
        self._cli("generate-building", "cyrillic.json", "--output", "cyrillic-output",
                  "--variants", "1", "--max-attempts", "1", "--time-limit", "3")
        self._run(
            "import json\n"
            "from pypdf import PdfReader\n"
            "manifest = json.loads(Path('cyrillic-output/manifest.json').read_text(encoding='utf-8'))\n"
            "sheets = Path('cyrillic-output') / manifest['variants'][0]['sheet_set']['pdf']\n"
            "text = '\\n'.join(page.extract_text() for page in PdfReader(sheets).pages)\n"
            "assert 'Фармацевтический завод' in text and 'аппарат' in text and 'помещение' in text\n"
        )

    def test_explicit_local_and_missing_absolute_paths_keep_their_meaning(self):
        # A bundled default must not replace a supplied existing file, or turn
        # an explicitly missing absolute path into a successful built-in load.
        work = self.root / "local-overrides"
        (work / "rules").mkdir(parents=True)
        baseline = (PROJECT / "rules" / "baseline.yaml").read_text(encoding="utf-8")
        (work / "rules" / "baseline.yaml").write_text(
            baseline.replace("name: Generic residential baseline", "name: Local project rules", 1),
            encoding="utf-8")
        output = self._run(
            "from layout_configurator.norms import load_ruleset\n"
            "assert load_ruleset('rules/baseline.yaml').name == 'Local project rules'\n"
            "try:\n"
            "    load_ruleset(Path('missing/rules/baseline.yaml').resolve())\n"
            "except FileNotFoundError:\n"
            "    print('explicit paths preserved')\n"
            "else:\n"
            "    raise AssertionError('A missing absolute path was replaced by bundled data')\n", cwd=work)
        self.assertIn("explicit paths preserved", output)


if __name__ == "__main__":
    unittest.main()
