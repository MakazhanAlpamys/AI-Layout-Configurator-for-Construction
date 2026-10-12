"""Opt-in real-browser proof through the pinned Playwright CLI.

Set FLC_PLAYWRIGHT_CLI to the installed playwright-cli executable. Every program
is entered from a blank form through visible controls; no state/JSON injection.
These checks stay outside the quick CI suite because they launch a browser and
the small regeneration case writes and independently reads real export files.
"""

import json
import hashlib
from importlib.metadata import version
import os
import platform
from pathlib import Path
import subprocess
import shutil
import tempfile
import threading
import unittest
import uuid

from layout_configurator.api import create_api_server, _runtime_fingerprint
from layout_configurator.bcf import read_bcf_package
from layout_configurator.bundle import write_facility_variant
from layout_configurator.building import BuildingIR
from layout_configurator.facility import default_facility_profile, load_facility_profile
from layout_configurator.facility_generation import solve_feasible_facility_variants
from layout_configurator.io import load_building, load_building_result
from layout_configurator.ifc import read_ifc_summary
from layout_configurator.qa import validate_building_set
from layout_configurator.walls import build_wall_plan


CLI = os.environ.get("FLC_PLAYWRIGHT_CLI")
PROJECT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(CLI, "Set FLC_PLAYWRIGHT_CLI for real browser acceptance")
class ProgramFormBrowserTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.server = create_api_server(self.root / "jobs", token="browser-fixture")
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        self.session = "form-" + uuid.uuid4().hex[:12]
        self.cli("open", self.base)
        self.addCleanup(lambda: self.cli("close"))

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def cli(self, *arguments):
        result = subprocess.run([CLI, "--session=" + self.session, *arguments],
                                cwd=self.root, capture_output=True, text=True, timeout=90)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("### Error", result.stdout, result.stdout)
        return result.stdout

    def script(self, code):
        path = self.root / "browser-action.js"
        path.write_text("async page => {\n" + code + "\n}", encoding="utf-8")
        return self.cli("run-code", "--filename=" + str(path))

    def enter(self, program, *, pilot=False):
        self.script("""
          await page.getByRole('textbox',{name:'Bearer token',exact:true}).fill('browser-fixture');
          await page.getByRole('button',{name:'Open program controls',exact:true}).click();
          await page.locator('#program-form').waitFor({state:'visible'});
        """)
        self.cli("snapshot")
        counts = [("Add rooms row", len(program["layout"]["rooms"])),
                  ("Add zones row", len(program["zones"])),
                  ("Add equipment row", len(program["equipment"])),
                  ("Add flows row", len(program["flows"]))]
        self.script("const counts=" + json.dumps(counts) + ";\n" + """
          for(const [name,count] of counts) for(let index=0;index<count;index++)
            await page.getByRole('button',{name,exact:true}).click();
        """ + ("await page.getByRole('checkbox',{name:'Include structural grid',exact:true}).check();" if "structural_grid" in program else ""))
        self.cli("snapshot")
        self.script("const fixture=" + json.dumps(program) + ";\n" + r"""
          async function fillFields(scope,values) {
            for(const [key,value] of Object.entries(values)) {
              const target=scope.locator(`[data-path$='.${key}']`);
              if(Array.isArray(value)) await target.fill(value.join('\n'));
              else if(typeof value==='boolean') await target.setChecked(value);
              else if(value!==null && typeof value!=='object') {
                if(await target.evaluate(node=>node.tagName)==='SELECT') await target.selectOption(String(value));
                else await target.fill(String(value));
              }
            }
          }
          await fillFields(page.locator('#layout-fields'),Object.fromEntries(Object.entries(fixture.layout)
            .filter(([key])=>!['rooms','doors','windows','boundary'].includes(key))));
          await page.locator(`[data-path='layout.boundary.width']`).fill(String(fixture.layout.boundary.width));
          await page.locator(`[data-path='layout.boundary.height']`).fill(String(fixture.layout.boundary.height));
          for(const [group,values] of [['layout.rooms',fixture.layout.rooms],['zones',fixture.zones],['equipment',fixture.equipment],['flows',fixture.flows]]) {
            const cards=page.locator(`[data-row='${group}']`);
            for(let index=0;index<values.length;index++) await fillFields(cards.nth(index),values[index]);
          }
          if(fixture.structural_grid) await fillFields(page.locator('#grid-fields'),fixture.structural_grid);
        """ + ("await page.getByRole('combobox',{name:'Rule profile',exact:true}).selectOption('pharma-cleanroom');" if pilot else ""))

    def validated_program(self):
        path = self.root / "canonical.json"
        self.script("""
          await page.getByRole('button',{name:'Validate program',exact:true}).click();
          await page.locator('#status').filter({hasText:'VALID INPUT'}).waitFor();
          const downloaded=page.waitForEvent('download');
          await page.getByRole('button',{name:'Download canonical program',exact:true}).click();
        """ + "await (await downloaded).saveAs(" + json.dumps(str(path)) + ");")
        return json.loads(path.read_text(encoding="utf-8"))

    def test_blank_browser_controls_reproduce_the_entire_canonical_pilot(self):
        program = load_building(PROJECT / "examples/pharma_cleanroom_pilot.yaml").to_dict()
        self.enter(program, pilot=True)
        self.assertEqual(self.validated_program(), program)
        self.script("""
          const widths=await page.locator(`[data-row='layout.rooms']`).first().evaluate(node=>
            [node.getBoundingClientRect().width,document.querySelector('#layout-fields').getBoundingClientRect().width]);
          if(widths[0]<widths[1]*0.75) throw new Error('Room controls do not use the available width');
          if(await page.evaluate(()=>localStorage.length||sessionStorage.length)) throw new Error('Browser data persisted');
          await page.getByRole('textbox',{name:'Project name',exact:true}).fill('Changed project');
          if(!await page.getByRole('button',{name:'Generate checked bundle',exact:true}).isDisabled()) throw new Error('Changed input can generate without validation');
          if(!await page.getByRole('button',{name:'Download canonical program',exact:true}).isDisabled()) throw new Error('Changed input can download stale canonical data');
        """)
        self.assertEqual(self.server.service.list(), [])

    def test_browser_generation_matches_direct_solver_in_the_same_runtime(self):
        building = BuildingIR.from_mapping({"version": "0.2", "layout": {
            "project_name": "Browser regeneration fixture", "boundary": {"width": 6000, "height": 6000},
            "rooms": [{"id": "work", "type": "work", "target_area": 9, "min_area": 9,
                       "max_area": 9, "min_width": 3000, "min_depth": 3000}],
        }})
        self.enter(building.to_dict())
        self.script("""
          await page.getByRole('spinbutton',{name:'Time limit (seconds)',exact:true}).fill('5');
          await page.getByRole('spinbutton',{name:'Deterministic work units (optional)',exact:true}).fill('5');
        """)
        self.assertEqual(self.validated_program(), building.to_dict())
        self.script("""
          await page.getByRole('button',{name:'Generate checked bundle',exact:true}).click();
          await page.locator('#job-status').filter({hasText:'SUCCEEDED'}).waitFor({timeout:30000});
          const job=JSON.parse(await page.locator('#job-evidence').textContent());
          if(!job.qa.ok || !job.qa.variants.every(item=>item.ok)) throw new Error('Generated QA failed');
          await page.locator('#artifacts button').first().waitFor();
          if(await page.locator('#job-checks li').count()<8) throw new Error('Readable gate evidence missing');
        """)
        job = self.server.service.list()[0]
        self.assertEqual(job["status"], "SUCCEEDED", job)
        self.assertTrue(self.server.service.check(job["id"])["ok"])
        accepted = load_building_result(self.root / "jobs" / job["id"] / "building_01.json")
        direct = solve_feasible_facility_variants(building, variants=1, max_attempts=1,
                    time_limit_seconds=5, seed=42, equipment_retries=2, deterministic_units=5,
                    room_solver="auto", profile=default_facility_profile())
        self.assertEqual(accepted[0].to_dict(), building.with_required_flow_opening_width().to_dict())
        self.assertEqual(accepted[1].to_dict(), direct.accepted[0].result.to_dict())
        self.assertEqual(accepted[2].to_dict(accepted[0]), direct.accepted[0].equipment.to_dict(building))

    @unittest.skipUnless(os.environ.get("FLC_BROWSER_PILOT_GENERATION"),
                         "Set FLC_BROWSER_PILOT_GENERATION for full pilot regeneration")
    def test_full_pilot_browser_and_direct_geometry_exchange_ids_and_qa_match(self):
        building = load_building(PROJECT / "examples/pharma_cleanroom_pilot.yaml")
        profile = load_facility_profile(PROJECT / "rules/pharma_cleanroom_pilot.yaml")
        fingerprint = self.server.service.fingerprint
        self.enter(building.to_dict(), pilot=True)
        self.script("""
          for(const [name,value] of [['Maximum attempts','3'],['Seed','1'],['Time limit (seconds)','30'],
            ['Deterministic work units (optional)','0.25']])
            await page.getByRole('spinbutton',{name,exact:true}).fill(value);
        """)
        self.assertEqual(self.validated_program(), building.to_dict())
        self.script("""
          await page.getByRole('button',{name:'Generate checked bundle',exact:true}).click();
          await page.waitForFunction(()=>/SUCCEEDED|FAILED|CANCELLED|INTERRUPTED/.test(document.querySelector('#job-status').textContent),{},{timeout:60000});
        """)
        job = self.server.service.list()[0]
        self.assertEqual(job["status"], "SUCCEEDED", job)
        browser_root = self.root / "jobs" / job["id"]
        browser_qa = self.server.service.check(job["id"])
        self.assertTrue(browser_qa["ok"], browser_qa)
        browser = load_building_result(browser_root / "building_01.json")
        generation = solve_feasible_facility_variants(building, profile, variants=1, max_attempts=3,
            time_limit_seconds=30, seed=1, equipment_retries=2, deterministic_units=.25, room_solver="auto")
        candidate = generation.accepted[0]
        prepared = building.with_required_flow_opening_width()
        self.assertEqual(browser[0].to_dict(), prepared.to_dict())
        self.assertEqual(browser[1].to_dict(), candidate.result.to_dict())
        self.assertEqual(browser[2].to_dict(prepared), candidate.equipment.to_dict(prepared))
        accepted_json = json.loads((browser_root / "building_01.json").read_text(encoding="utf-8"))
        self.assertEqual(accepted_json["flow_routes"], candidate.flow_routes.to_dict())
        browser_walls = build_wall_plan(prepared.layout, browser[1])
        direct_walls = build_wall_plan(prepared.layout, candidate.result)
        self.assertEqual(browser_walls.openings, direct_walls.openings)
        self.assertEqual(browser_walls.windows, direct_walls.windows)
        self.assertEqual(browser_walls.rings(), direct_walls.rings())
        direct_root = self.root / "direct"
        entry = write_facility_variant(direct_root, prepared, profile, candidate.result,
            layout_report=candidate.layout_report, equipment=candidate.equipment,
            equipment_report=candidate.equipment_report, flow_routes=candidate.flow_routes,
            flow_report=candidate.flow_report, facility_report=candidate.facility_report,
            generation_evidence=candidate.generation_evidence())
        manifest = {"project": prepared.layout.project_name, "profile": profile.to_dict(),
                    "generation": generation.to_dict(), "variants": [entry]}
        (direct_root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        direct_qa = validate_building_set(direct_root, expected_variants=1, profile=profile).to_dict()
        self.assertTrue(direct_qa["ok"], direct_qa)
        browser_ifc = read_ifc_summary(browser_root / "building_01.ifc").to_dict()
        direct_ifc = read_ifc_summary(direct_root / "building_01.ifc").to_dict()
        browser_ifc.pop("path"); direct_ifc.pop("path")
        self.assertEqual(browser_ifc, direct_ifc)
        import ifcopenshell
        import ifcopenshell.validate
        ifc_id_sets = []
        for root in (browser_root, direct_root):
            model = ifcopenshell.open(str(root / "building_01.ifc"))
            ids = [(item.is_a(), item.GlobalId, item.Name) for item in model.by_type("IfcRoot")]
            self.assertEqual(len({item[1] for item in ids}), len(ids))
            self.assertGreater(len(ids), 50)
            ifc_id_sets.append(sorted(ids))
            logger = ifcopenshell.validate.json_logger()
            ifcopenshell.validate.validate(model, logger, express_rules=True)
            self.assertEqual(logger.statements, [], logger.statements)
        self.assertEqual(ifc_id_sets[0], ifc_id_sets[1])
        browser_topics = read_bcf_package(browser_root / "building_01.bcf").topics
        direct_topics = read_bcf_package(direct_root / "building_01.bcf").topics
        self.assertEqual([topic.to_dict() for topic in browser_topics], [topic.to_dict() for topic in direct_topics])
        self.assertEqual(_runtime_fingerprint(), fingerprint, "Source/dependencies changed during the comparison")
        self.assertEqual(job["runtime"], fingerprint)
        if os.environ.get("FLC_BROWSER_ARTIFACT_DIR"):
            output = Path(os.environ["FLC_BROWSER_ARTIFACT_DIR"])
            output.mkdir(parents=True, exist_ok=True)
            shutil.copytree(browser_root, output / "pilot-browser", dirs_exist_ok=True)
            shutil.copytree(direct_root, output / "pilot-direct", dirs_exist_ok=True)
            shutil.copy2(self.root / "canonical.json", output / "pilot-browser-program.json")
            evidence = {"status": "PASS", "scope": "Same-runtime browser/direct semantic and generated-model comparison; no engineering approval",
                "runtime_fingerprint": fingerprint, "platform": platform.platform(), "python": platform.python_version(),
                "dependencies": {name: version(name) for name in ("ortools", "shapely", "ezdxf", "reportlab", "pypdf", "ifcopenshell", "jsonschema")},
                "canonical_input_sha256": hashlib.sha256(json.dumps(building.to_dict(), sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()).hexdigest(),
                "downloaded_source_sha256": hashlib.sha256((self.root / "canonical.json").read_bytes()).hexdigest(),
                "settings": job["settings"], "browser_qa": browser_qa, "direct_qa": direct_qa,
                "ifc_root_guids": len(ifc_id_sets[0]), "bcf_topics": len(browser_topics),
                "identical": ["canonical_input", "prepared_program", "room_geometry", "wall_openings", "equipment_geometry",
                              "flow_points_and_topology", "ifc_readback_ids", "bcf_topic_and_viewpoint_ids"],
                "excluded": ["timestamps", "job_paths", "PDF_bytes"], "engineering_acceptance": "PENDING"}
            (output / "pilot-regeneration-proof.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")
            self.script("await page.locator('#job').scrollIntoViewIfNeeded(); await page.screenshot({path:" + json.dumps(str(output / "program-form-full-pilot-job.png")) + "});")
