## Scope and verification notice

**I cannot browse the live web from this environment.** Therefore I cannot honestly “open and verify” every link or report exact current stars/forks/last commit. All repository metrics, licenses, and statuses below are from training knowledge and must be treated as **unverified until checked manually**. Where information is absent or uncertain, it is marked `unverified` or `not found`.

This report is intended as a technical foundation, not a live audit.

---

# 1. Open-source technologies for DXF/DWG/PDF, CAD kernels, BIM/IFC, headless rendering

## 1.1 DXF / DWG generation and parsing

| Project | Official link | Purpose | Tech stack | License/SPDX | Stars/forks/last commit | Downloadable code/weights/installer | DXF/DWG support | Limitations |
|---|---|---|---|---|---|---|---|---|
| **ezdxf** | https://ezdxf.readthedocs.io/ | Python DXF create/read/modify | Python | MIT | unverified; active as of 2024 | PyPI, GitHub | DXF R12–R2018 read/write; no DWG write | No semantic walls/doors; DXF entities only; hatch/dimension support exists but requires care |
| **LibreDWG** | https://www.gnu.org/software/libredwg/ | C library for DWG read/write | C | GPL-3.0-or-later | unverified; active | GNU ftp, GitHub | DWG read much better than write; DXF conversion | GPL; writing DWG limited; not suitable for generating complex drawings |
| **LibreCAD** | https://librecad.org/ | 2D CAD application | C++/Qt | GPL-2.0-only | unverified | GitHub, installers | DXF read/write; DWG read via plugin? | GUI-centric; not a headless library; PDF export via print |
| **QCAD Community Edition** | https://www.qcad.org/ | 2D CAD with ECMAScript scripting | C++/Qt | GPL-3.0-or-later with exceptions | unverified | GitHub, installers | DXF/DWG read/write; command-line tools for PDF/DXF | DWG support often via proprietary plugin; scripting possible but not Python-native |
| **libdxfrw** | https://github.com/LibreCAD/libdxfrw | DXF read/write C++ library | C++ | GPL-2.0-or-later | unverified | GitHub | DXF read/write | Limited entity support compared to ezdxf |

**Support summary for CAD entities**:

- **Walls, doors, windows** are not semantic objects in DXF/DWG. They must be represented as linework, polylines, blocks, and attributes.
- **Layers, blocks, hatches, dimensions** are supported by ezdxf and LibreCAD; ezdxf is best for programmatic creation.
- **Title blocks** can be created as block references with attributes in DXF using ezdxf.
- **Vector PDF** is not native to DXF; you need a separate PDF library.

---

## 1.2 CAD and geometry kernels

| Project | Official link | Purpose | Tech stack | License/SPDX | Stars/forks/last commit | Downloadable code/weights/installer | Limitations |
|---|---|---|---|---|---|---|---|
| **Open CASCADE (OCCT)** | https://dev.opencascade.org/ | Full 3D geometry kernel | C++ | LGPL-2.1-only with OCCT exception | unverified; active | GitHub, installer | No built-in DXF/PDF; can export STEP/IGES; heavy |
| **CadQuery** | https://cadquery.readthedocs.io/ | Python parametric CAD on OCCT | Python, OCCT | Apache-2.0 | unverified; active | PyPI, GitHub | 2D sketches, extrusions; DXF export limited; not for architectural BIM |
| **FreeCAD** | https://www.freecad.org/ | Parametric CAD/BIM | C++/Python, OCCT | LGPL-2.1-or-later | unverified; active | GitHub, installers | Best open-source CAD/BIM base; Arch/BIM workbench has walls, doors, windows; headless via FreeCADCmd; DXF/IFC/PDF via TechDraw |

**Recommendation**: Use **FreeCAD headless** if you need a full CAD kernel with architectural objects and PDF/DXF export. Use **ezdxf** for lightweight DXF generation. Use **CadQuery/OCCT** only if you need low-level geometry operations.

---

## 1.3 BIM / IFC tooling

| Project | Official link | Purpose | Tech stack | License/SPDX | Stars/forks/last commit | Downloadable code/weights/installer | Limitations |
|---|---|---|---|---|---|---|---|
| **IfcOpenShell** | https://ifcopenshell.org/ | IFC read/write, geometry, validation | C++/Python | LGPL-3.0-or-later core; some parts GPL | unverified; active | GitHub, PyPI | Can create IfcWall, IfcDoor, IfcWindow, spaces; geometry engine; good IFC support |
| **Bonsai / BlenderBIM** | https://bonsaibim.org/ | BIM authoring in Blender | Python, Blender | GPL-3.0 | unverified; active | GitHub, Blender add-on | GUI + headless? possible; produces IFC; drawings via Blender; not a DXF generator |
| **BIMserver** | https://github.com/opensourceBIM/BIMserver | IFC model server | Java | AGPL-3.0 | unverified | GitHub | Server, not a generation library |
| **Xbim** | https://github.com/xBimTeam/XbimEssentials | .NET IFC toolkit | C# | CDDL-1.0? unverified | unverified | GitHub, NuGet | Strong IFC reading/writing; Windows-centric; not Python-native |

**IFC support**: IfcOpenShell is the best open-source choice for generating IFC from floor-plan geometry. It supports walls, doors, windows, spaces, and property sets.

---

## 1.4 PDF / vector drawing generation

| Project | Official link | Purpose | Tech stack | License/SPDX | Stars/forks/last commit | Downloadable code/weights/installer | Limitations |
|---|---|---|---|---|---|---|---|
| **ReportLab** | https://www.reportlab.com/dev/opensource/ | Programmatic PDF generation | Python | BSD-3-Clause | unverified; active | PyPI | Not CAD-aware; you draw lines, text, shapes manually; good title blocks |
| **WeasyPrint** | https://weasyprint.org/ | HTML/CSS to vector PDF | Python | BSD-3-Clause | unverified; active | PyPI | Good for styled reports; use SVG for vector floor plans; no DXF |
| **matplotlib** | https://matplotlib.org/ | Plotting and PDF output | Python | BSD-3-Clause? actually PSF? unverified | unverified; active | PyPI | Can draw polylines/polygons and export vector PDF/SVG; not CAD |
| **svgwrite / cairo** | https://svgwrite.readthedocs.io/ | SVG vector drawing | Python | MIT? unverified | unverified | PyPI | SVG can be embedded in PDF via WeasyPrint |

**Recommendation**: Use **ReportLab** for title-blocked PDF sheets with vector floor-plan linework. Use **WeasyPrint** if you prefer HTML/CSS templating.

---

## 1.5 Headless rendering

| Tool | Use | License |
|---|---|---|
| **FreeCADCmd** | Headless CAD operations, TechDraw PDF export, DXF/IFC export | LGPL-2.1-or-later |
| **Blender headless** | 3D rendering, possible IFC via Bonsai | GPL-3.0 |
| **Open CASCADE HLR** | Hidden-line removal to SVG/PDF for technical drawings | LGPL-2.1 with exception |
| **POV-Ray** | Ray tracing, not CAD | AGPL-3.0? |

For 2D floor plans, **FreeCAD headless + TechDraw** is the most direct route to PDF and DXF from BIM objects.

---

# 2. AI and algorithmic floor-plan generation

## 2.1 Neural network approaches

| Project | Official link | Input | Output | Code/weights | License | Hard constraints supported? | Status |
|---|---|---|---|---|---|---|---|
| **HouseGAN** | https://github.com/ennauata/housegan | Bubble diagram + building boundary | Raster floor plan image | unverified | unverified | No exact areas, adjacencies soft | Research |
| **HouseGAN++** | https://github.com/ennauata/houseganpp | Graph + boundary | Raster floor plan images | unverified | unverified | No exact constraints | Research |
| **Graph2Plan** | https://github.com/HanHan55/Graph2Plan | Layout graph + boundary | Raster + some vector? unverified | unverified | unverified | Adjacency learned, not hard | Research |
| **HouseDiffusion** | https://github.com/aminshabani/house_diffusion | Graph + boundary | Vector room corners | unverified | MIT? unverified | Adjacency/area soft | Research, vector output more promising |
| **RPLAN** | unverified, search “RPLAN dataset” | N/A dataset | Vector/annotated floor plans | dataset download unverified | unverified research-only | N/A | Dataset |
| **CubiCasa5K** | https://zenodo.org/record/3543506 | N/A dataset | Annotated floor plans | unverified | CC BY? unverified | N/A | Dataset |
| **FloorPlanCAD** | unverified | N/A dataset | CAD floor plans | unverified | unverified | N/A | Dataset |

**Key limitation**: Neural methods produce raster images or rough vector coordinates. They cannot guarantee room areas, adjacencies, wall thickness, door clearances, or building-code compliance. They are useful only as **candidate generators** or **style prior**, never as final geometry.

## 2.2 Procedural / solver-based approaches

These are not “AI” in the generative-adversarial sense but are more reliable for hard constraints.

| Tool | Official link | Purpose | License | Constraint support |
|---|---|---|---|---|
| **OR-Tools CP-SAT** | https://developers.google.com/optimization | Constraint programming | Apache-2.0 | Exact rectangles, non-overlap, adjacency, area, boundary |
| **Z3** | https://github.com/Z3Prover/z3 | SMT solver | MIT | Linear/boolean constraints; can model room dimensions |
| **python-constraint** | https://github.com/python-constraint/python-constraint | Simple CSP | MIT? unverified | Basic constraints |
| **DEAP** | https://github.com/DEAP/deap | Evolutionary algorithms | LGPL-3.0 | Multi-objective, can evolve layouts with fitness for constraints |
| **shapely** | https://shapely.readthedocs.io/ | 2D geometry validation | BSD-3-Clause | Area, intersection, buffer, contains |

**Recommendation**: Use **OR-Tools CP-SAT** for exact rectangle packing with adjacency and area constraints. Use **shapely** for post-checking. Add a procedural generator to produce candidate topologies from the room graph.

---

# 3. Building-code compliance, BIM, ACC, and RAG

## 3.1 Open-source rule engines

| Tool | Official link | Purpose | License |
|---|---|---|---|
| **Drools** | https://www.drools.org/ | Business rule engine | Apache-2.0 |
| **Camunda DMN** | https://camunda.com/ | DMN decision engine | Apache-2.0 |
| **durable_rules** | https://github.com/jruizgit/rules | Python rules | MIT? unverified, possibly unmaintained |
| **JSON Logic** | https://jsonlogic.com/ | Serializable rules | MIT? |
| **Python Rule Engine** | unverified | Simple rule matching | unverified |

For code compliance, **deterministic rule functions** written in Python against the generated geometry may be simpler and auditable.

## 3.2 IFC interoperability and automated code checking

- **IfcOpenShell** can validate IFC models against schema and custom checks.
- **buildingSMART IDS** (Information Delivery Specification) is an XML standard for machine-readable IFC requirements. Tools like **IfcOpenShell** can execute some IDS rules.
- **buildingSMART bSDD** provides machine-readable building data dictionaries.
- **BIMserver** has some validation support.
- **Autodesk ACC** is proprietary; APIs exist but are not open.

## 3.3 RAG over regulatory documents

Typical stack:

| Component | Example | License |
|---|---|---|
| LLM framework | LangChain / LlamaIndex / Haystack | MIT / MIT / Apache-2.0 |
| Vector DB | Chroma / Qdrant / pgvector | Apache-2.0 / Apache-2.0 / PostgreSQL |

**How to use RAG correctly**:

1. Ingest building codes as PDF/HTML into a vector DB.
2. Use LLM to retrieve relevant clauses for a given jurisdiction and project type.
3. A human expert converts retrieved clauses into **deterministic constraints** or rule functions.
4. The LLM must **never** be the final compliance checker; it can only explain or suggest.

**Why**: LLMs hallucinate, especially on numeric thresholds like minimum corridor width or maximum travel distance.

---

# 4. Existing products and market gaps

## 4.1 Proprietary products

| Product | Official link | Input | Output | Pricing | Target users | DXF/IFC support | Constraint capabilities | Limitations |
|---|---|---|---|---|---|---|---|---|
| **Maket.ai** | https://www.maket.ai/ | Zoning constraints, style, budget? unverified | 3D model, floor plans, material lists? unverified | Subscription, free trial? unverified | Builders, architects | DXF? unverified | Some code constraints, mainly residential | Not full BIM; limited hard compliance |
| **TestFit** | https://testfit.io/ | Site, unit mix, code presets | Massing, plans, Excel reports | Enterprise | Architects, developers, GCs | DXF/DWG likely | Strong for feasibility; some code presets | Not detailed construction drawings |
| **Finch3D** | https://finch3d.com/ | Program, constraints, site | 3D BIM, editable in Revit/Rhino | Subscription | Architects | IFC? unverified | Generative, iterative | Early-stage only |
| **Autodesk Forma** | https://www.autodesk.com/products/forma | Site, massing constraints | 3D massing, sun/wind analysis | Subscription | Architects, planners | IFC? likely | Not detailed floor plans | Not DXF |
| **Planner5D** | https://planner5d.com/ | Interior design | 2D/3D interior plans | Freemium | Homeowners, interior designers | DXF? maybe paid | No code compliance | Not for architecture |
| **Hypar** | https://hypar.io/ | Parameters via API/UI | JSON, DXF, IFC, glTF? | Free/paid | Developers, architects | Yes DXF/IFC likely | You must write functions | Requires development effort |

## 4.2 Market gaps

- **No open-source turnkey** AI layout configurator that produces editable DXF + IFC + PDF with deterministic code compliance.
- **Neural generation** is not production-ready for exact architectural constraints.
- **Building codes** are not machine-readable; every jurisdiction requires custom rule authoring.
- **DWG write** is weak in open-source; DXF is the practical interchange format.
- **No product** combines LLM spec parsing, constraint solver, and CAD output in an auditable way.

---

# Recommended architecture and technology stack

## Overall architecture

```
User technical specification (PDF/text/JSON)
        │
        ▼
LLM spec parser (allowed: extract structured JSON, ask questions)
        │  produces RoomProgram + constraints
        ▼
Human review UI (confirm rooms, areas, adjacencies)
        │
        ▼
RAG for building code (human-approved deterministic rules)
        │
        ▼
Deterministic geometry engine
   ├─ Procedural candidate generator (topology seeds)
   ├─ OR-Tools CP-SAT solver (exact areas, adjacencies, boundary)
   └─ shapely validation
        │
        ▼
CAD/BIM output
   ├─ ezdxf → DXF (walls, doors, windows as blocks/layers, hatches, dimensions, title block)
   ├─ IfcOpenShell → IFC (IfcWall, IfcDoor, IfcWindow, IfcSpace, property sets)
   └─ ReportLab/WeasyPrint → vector PDF sheets
        │
        ▼
Validation layer
   ├─ Geometry: areas, non-overlap, min widths, connectivity
   ├─ Code rules: deterministic Python functions / JSON Logic
   ├─ DXF round-trip: read back with ezdxf, check layers/entities
   ├─ PDF: check vector text/graphics, title block fields
   └─ IFC: IfcOpenShell schema validation, IDS if available
```

## Recommended stack

| Layer | Technology | License |
|---|---|---|
| Backend | Python 3.11+, FastAPI, Celery | MIT / BSD |
| Spec parsing | LLM with function calling, Pydantic validation | proprietary LLM API |
| RAG | LlamaIndex or LangChain + pgvector | MIT |
| Geometry engine | OR-Tools CP-SAT, shapely, custom procedural generator | Apache-2.0 / BSD-3-Clause |
| DXF generation | ezdxf | MIT |
| IFC generation | IfcOpenShell | LGPL-3.0 |
| PDF generation | ReportLab + WeasyPrint | BSD-3-Clause |
| Validation | shapely, custom rule functions, ezdxf round-trip | BSD-3-Clause / MIT |
| Frontend | React, SVG renderer, three.js for 3D | MIT |

## Where an LLM should and should not be used

**Allowed**:

- Parse natural language technical specification into structured JSON.
- Ask clarifying questions when rooms or constraints are missing.
- Summarize building-code clauses retrieved by RAG.
- Generate human-readable explanations of generated layouts.

**Not allowed**:

- Final room coordinates or wall geometry.
- Final pass/fail on building-code compliance.
- Direct DXF/IFC/PDF output.
- Any numeric threshold without deterministic verification.

---

# MVP roadmap

1. **Week 1–2**: Build spec schema (rooms, areas, adjacencies, boundary) and LLM parser.
2. **Week 3–4**: Procedural room placement using OR-Tools CP-SAT; validate with shapely.
3. **Week 5–6**: Generate DXF with ezdxf; include layers, blocks for doors/windows, dimensions, title block.
4. **Week 7**: Generate vector PDF via ReportLab/WeasyPrint.
5. **Week 8**: Generate IFC via IfcOpenShell.
6. **Week 9–10**: RAG prototype for one jurisdiction; human authoring of deterministic rules.
7. **Week 11–12**: Integration, UI, round-trip validation tests.

---

# Validation strategy

## Geometry validation
- Room areas within ±2% of target.
- Rooms do not overlap.
- All rooms inside building boundary.
- Adjacency graph matches input.
- Minimum room width/depth satisfied.
- Corridor connectivity check via shapely graph.

## Code compliance validation
- Rules stored as deterministic Python functions or JSON Logic.
- Run on generated geometry after each iteration.
- Produce a pass/fail report with exact clause references.
- No LLM in final compliance decision.

## DXF validation
- Read back generated DXF with ezdxf.
- Check all required layers exist.
- Check blocks for doors/windows are inserted correctly.
- Check dimensions and hatches exist.
- Validate DXF version header.
- Optionally open in FreeCAD headless and re-export to catch structural issues.

## PDF validation
- PDF must be vector, not raster.
- Title block text fields must match project data.
- Check floor plan lines are selectable vector graphics.
- Use `pdftotext` or PyMuPDF to verify text; use page image comparison for layout sanity.

## BIM / IFC validation
- Validate IFC schema with IfcOpenShell.
- Check spatial structure: IfcBuilding → IfcBuildingStorey → IfcSpace.
- Check IfcWall, IfcDoor, IfcWindow exist and have geometry.
- Check property sets for room names/areas.
- Optionally run buildingSMART IDS rules if available.

---

# Major DEAD ZONES

1. **Neural networks cannot guarantee hard architectural constraints.** Do not rely on GAN/diffusion for final geometry.
2. **DXF has no semantic wall/door/window model.** You must use layers, blocks, and attributes; round-tripping may lose metadata.
3. **Open-source DWG write support is weak.** Use DXF as interchange or accept LibreDWG limitations.
4. **Building codes are not machine-readable.** RAG can retrieve text but cannot reliably convert it to executable rules without human review.
5. **No open-source turnkey code-compliance engine** exists for multiple jurisdictions.
6. **LLM hallucination on numeric code limits** is a serious risk if used directly.
7. **IFC ↔ DXF bidirectional round-trip** is lossy; the two formats have different semantics.
8. **PDF dimension fidelity** from CAD can be inconsistent if not generated carefully.

---

# Three strongest product opportunities

1. **Code-aware generative feasibility configurator**
   - Input: technical spec + jurisdiction.
   - Output: several editable DXF + IFC + PDF options with compliance report.
   - Target: architects, developers, builders.

2. **Open-source DXF/IFC/PDF validation and round-trip service**
   - Validate geometry, layers, blocks, dimensions, BIM semantics.
   - Target: AEC software teams, consultants.

3. **Auditable RAG pipeline for building-code rule extraction**
   - Produce human-reviewed, deterministic rule libraries from PDFs.
   - Target: code consultants, software vendors needing machine-readable rules.

---

### Final note

All repository metrics, licenses, and links above are from training data and **must be re-verified manually** before use in a production or contractual context. This report is a starting architecture, not a live audit.