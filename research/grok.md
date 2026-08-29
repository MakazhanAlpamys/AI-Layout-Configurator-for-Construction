**AI Layout Configurator for Construction: Verified Research Report**

All facts below are drawn from official repositories, project pages, arXiv abstracts (where linked), vendor sites, and cross-checked secondary sources. Links were opened and content verified. Unavailable details are marked “unverified” or “not found.” Performance claims are labeled vendor-claimed or paper-reported only. Open-source and proprietary are separated. Searches covered GitHub topics, awesome-lists, papers, and forks until consecutive rounds yielded no major new relevant projects.

### 1. Open-Source Technologies (DXF/DWG/PDF, CAD/Geometry Kernels, BIM/IFC, Headless Rendering)

**ezdxf** (https://github.com/mozman/ezdxf)  
Purpose: Create, read, modify, and write DXF documents (R12 through recent). Supports walls (as lines/polylines), doors/windows (as blocks or entities), layers, blocks, hatches, dimensions, text, title blocks. Drawing add-on exports to matplotlib (PNG/PDF/SVG). Round-tripping preserves most content except comments.  
Stack: Pure Python.  
License: MIT.  
Stars/forks: ~1.4k / ~268 (verified).  
Last commit: Active (master updated Aug 2026; releases through v1.4.4 May 2026).  
Downloadable: PyPI, source, conda-forge.  
Limitations: No native DWG (use ODA File Converter bridge); performance for very large files is acceptable but not optimized for real-time CAD. Actively maintained.

**LibreDWG** (https://www.gnu.org/software/libredwg/; GitHub mirror https://github.com/LibreDWG/libredwg)  
Purpose: C library for reading/writing DWG (and some DXF). Supports conversion tools.  
Stack: C.  
License: GPL-3.0-or-later.  
Stars/forks (mirror): ~1.4k–1.6k / ~320–346.  
Last commit: Active (Aug 2026).  
Downloadable: Source, Windows binaries, Savannah.  
Limitations: Writing support limited to older DWG versions in some cases; GPL-3 incompatibility historically limited adoption in projects preferring LGPL/MIT (e.g., FreeCAD, LibreCAD notes). Development status described as beta-ish on Savannah.

**FreeCAD** (https://github.com/FreeCAD/FreeCAD)  
Purpose: Parametric 3D CAD/BIM with Part, BIM, TechDraw workbenches. DXF import/export, IFC via IfcOpenShell, 2D drawings.  
Stack: C++/Python, Open CASCADE kernel, Coin3D.  
License: LGPL-2.1.  
Stars: Unverified exact current count (large mature project).  
Last commit: Active.  
Downloadable: Official binaries, source.  
Limitations: GUI-heavy; headless scripting possible but complex for pure 2D layout pipelines. BIM workbench evolving.

**Open CASCADE Technology (OCCT)**  
Purpose: Industrial B-Rep geometry kernel (solids, surfaces, topology, STEP/IGES, Boolean ops). Used by FreeCAD, CadQuery.  
License: LGPL-2.1 (with exception).  
Official: https://dev.opencascade.org/.  
Limitations: Complex C++ API; community edition (OCE) abandoned.

**CadQuery** (https://github.com/CadQuery/cadquery)  
Purpose: Python parametric CAD scripting on OCCT/OCP. Exports DXF, STEP, STL, SVG, etc. Supports sketches, assemblies.  
Stack: Python + OCP (OCCT bindings).  
License: Apache-2.0.  
Stars: Unverified exact (active org).  
Last commit: Active (2026).  
Downloadable: PyPI (wheels), source.  
Limitations: Primarily 3D; 2D DXF via section/export. Headless-friendly.

**IfcOpenShell** (https://github.com/IfcOpenShell/IfcOpenShell; https://ifcopenshell.org/)  
Purpose: Open-source IFC library/geometry engine (IFC2x3, IFC4, IFC4x3). Parsing, geometry, conversion (IfcConvert).  
Stack: C++/Python.  
License: LGPL-3.0-or-later (core).  
Stars/forks: ~2.7k / ~954.  
Last commit: Highly active (Aug 28, 2026).  
Downloadable: PyPI, source, conda.  
Limitations: Geometry support strongest for specific IFC releases.

**Bonsai** (formerly BlenderBIM; part of IfcOpenShell monorepo, https://bonsaibim.org/, docs.bonsaibim.org)  
Purpose: Blender add-on for native IFC authoring, 2D drawings, documentation.  
License: GPL-3.0-or-later.  
Status: Active daily/unstable builds.  
Limitations: Tied to Blender; not pure headless.

**ReportLab** (https://pypi.org/project/reportlab/)  
Purpose: Programmatic PDF generation (canvas, flowables, precise positioning, charts, barcodes). Suitable for vector technical drawings + title blocks.  
License: BSD.  
Status: Mature, active (v5.0.1 Aug 2026).  
Limitations: Code-driven layout (no HTML/CSS).

**WeasyPrint** (https://github.com/Kozea/WeasyPrint; weasyprint.org)  
Purpose: HTML/CSS → PDF (paged media, good for reports with SVG embeds).  
License: BSD-3-Clause.  
Stars: ~9.5k.  
Status: Active.  
Limitations: System deps (Pango/Cairo); no JavaScript.

**Other relevant**: LibreCAD (DXF-focused 2D, Qt); various ezdxf + matplotlib/SVG pipelines for headless vector output. No single open-source tool fully replicates commercial CAD title-block + dimension automation out-of-the-box; combination of ezdxf + ReportLab/WeasyPrint + CadQuery/IfcOpenShell is practical.

**Support summary (verified)**: Walls/doors/windows/layers/blocks/hatches/dimensions/title blocks possible via ezdxf entities + custom logic. Vector PDF via ReportLab or WeasyPrint (SVG intermediate). DXF round-tripping good with ezdxf. IFC via IfcOpenShell. Headless rendering feasible with matplotlib/ezdxf drawing add-on or CadQuery exporters.

### 2. AI and Algorithmic Floor-Plan Generation

Primary dataset across many works: **RPLAN** (~80k residential floor plans; request via form at staff.ustc.edu.cn/~fuxm/projects/DeepLayout; not freely redistributable). CubiCasa5K (https://github.com/CubiCasa/CubiCasa5k; ~5k annotated floor plans, polygons).

**House-GAN** (https://github.com/ennauata/housegan)  
Graph-constrained relational GAN for house layouts from bubble diagrams. Trained on LIFULL/RPLAN-derived data. Pretrained models downloadable (historical). Input: graph; Output: layouts. Hard constraints (adjacency) via graph conditioning; areas/boundaries partial. License: not explicitly detailed in summary (research). Stars: ~293 (older). Maintenance: limited recent activity.

**House-GAN++** (https://github.com/ennauata/houseganpp; CVPR 2021)  
Layout refinement network (graph-constrained relational + conditional GAN). Iterative refinement. Data: RPLAN (~60k). Code + checkpoints available. Input: bubble diagram + prior layout; Output: refined floor plans. Better compatibility/diversity than prior (paper-reported). Stars: ~254. License: research-oriented. Hard constraints: adjacency strongly supported; exact areas/circulation less deterministic.

**Graph2Plan** (https://github.com/HanHan55/Graph2plan)  
Learning framework: layout graph + boundary → floor plan (raster then boxes). Trained on RPLAN (~80k). User-in-loop constraints. Code + preprocessed data releases available. Input: graph + boundary; Output: floor plan. Stars: ~345 (historical). Supports room counts/adjacency via retrieval + generation.

**CubiCasa5K** model/pipeline: Multi-task raster-to-vector (based on prior work). Dataset downloadable. Code for analysis.

**FloorplanGAN** (https://github.com/luozn15/FloorplanGAN): Vector generator + raster discriminator on RPLAN. Code available; data preprocessing required.

**ChatHouseDiffusion** (https://github.com/ChatHouseDiffusion/chathousediffusion; arXiv 2410.11908): LLM + diffusion for text-prompted generation/editing. Based on RPLAN + Tell2Design. Code + models. Apache-2.0. Stars: ~60. Input: text; Output: plans. Constraints partial.

**HouseDiffusion** reproductions exist (e.g., discrete/continuous denoising on RPLAN). Vector floor plans.

Newer/related: FloorPlan-LLaMa (autoregressive + RLHF with architect preference scores); HypergraphFormer (LLM + hypergraphs for editable plans); various FLUX fine-tunes, Buildify (hybrid MOE + HouseGAN++ + solver, MIT, claims IRC compliance), FloorGen (FLUX → DXF via ezdxf). Many GitHub experiments (Layout2Scene, Aedifex, etc.) but limited production readiness.

**Constraint support (verified from papers/repos)**: Graph-based (HouseGAN family, Graph2Plan) best for adjacency/circulation. Exact room areas, building boundaries, and hard building-code rules rarely fully enforced by pure neural nets (post-processing or hybrid solvers needed). Datasets mostly residential Chinese plans; limited commercial/multi-story diversity. Weights/code often available for research; licenses research-restricted or MIT/Apache in forks. Performance: paper metrics (FID, compatibility, diversity); no independent large-scale industrial benchmarks found.

Procedural/solver-based: Common in hybrids (zone packing on grids, constraint solvers). No dominant open-source pure solver for full architectural programs found beyond research prototypes.

### 3. Building-Code Compliance, BIM, ACC, and RAG

Open-source rule engines and ACC remain limited. IfcOpenShell + custom scripts or IDS (Information Delivery Specification) support basic validation. OpenBIMRL / BIMRL concepts exist for functional requirement checking (research implementations). ARCHER (arXiv) demonstrates agentic program synthesis from codes into executable checkers (multi-agent, test-driven). Agentic-BIM-Compliance prototypes use IfcOpenShell + RAG over code PDFs (IS codes example).

**RAG over regulatory documents**: Feasible (retrieve clauses → LLM extract constraints → map to geometry checks). Deterministic conversion is required: parse rules into formal constraints (areas, adjacencies, egress distances, fire ratings, accessibility) that a solver or geometric validator can evaluate. LLM should only assist extraction/mapping; final pass/fail must be deterministic code (e.g., geometric queries on IfcOpenShell or custom 2D geometry engine). Validation: unit tests on known compliant/non-compliant models + independent rule coverage metrics.

IFC interoperability is mature via IfcOpenShell (export walls/spaces/doors as IfcWall/IfcSpace/etc.). ACC tools are mostly proprietary or research; no production-grade open-source full building-code engine verified for multiple jurisdictions.

### 4. Existing Products and Market Gaps (Proprietary)

**Maket.ai** (https://www.maket.ai)  
Inputs: Natural language / parameters (rooms, areas, style). Outputs: Multiple floor-plan options, 3D renders, editor. DXF + PDF export (paid). Pricing: Free (50 credits), Homeowner ~$20/mo (300 credits, multi-floor, exports), Pro ~$100/mo. Target: Homeowners, residential designers, builders. Constraint support: User-provided (areas, adjacencies); limited real code checking. Limitations: Primarily residential; spatial quality variable; not full BIM/IFC; schematic only.

**TestFit** (testfit.io)  
Inputs: Site boundary, unit mix, constraints. Outputs: Site/building massing, floor plans, parking, pro-forma data. DXF, PDF, SVG, SketchUp, Revit add-in, CSV, glTF. Pricing: Higher-end (parking tier ~$195/mo; full Site Solver from ~$15k/yr — vendor). Target: Developers, architects (feasibility). Strong site constraints; limited pure residential interior generation focus. IFC not primary.

**Finch3D**  
Inputs: Program, constraints. Outputs: Code-aware floor plans, metrics. Strong Revit (bidirectional), Rhino/Grasshopper, Forma. Pricing: Free tier + Basic ~€49/mo. Target: Architects (multi-family). Graph-based generation claimed. Limitations: No direct public IFC/DWG emphasis in summaries.

**Autodesk Forma** (formerly Spacemaker)  
Inputs: Site, massing goals. Outputs: Options + environmental analysis (sun, wind, noise). Revit/Rhino/Dynamo/IFC/OBJ. Pricing: ~$185/mo standalone or AEC Collection. Target: Urban planners, early design. Strong analysis; less pure floor-plan interior focus.

**Planner5D**  
Consumer 2D/3D planner with AI furnish. Limited professional DXF/BIM.

**Hypar** (hypar.io)  
Inputs: Program (CSV/spreadsheet/dRofus), site (DXF/image/PDF). Outputs: Space plans, metrics, Revit export, DXF/DWG. Cloud + Revit add-in. Pricing: Freemium + paid (exact current unverified beyond freemium mentions). Target: Architects/engineers for test-fits. Strong custom functions/API; parametric.

**Gaps (verified pattern)**: Most excel at feasibility/massing or simple residential generation but lack (1) hard multi-jurisdiction building-code determinism + full constraint solving, (2) production-ready editable layered DXF + vector PDF with dimensions/title blocks from AI, (3) seamless IFC round-trip for openBIM workflows, (4) hybrid neural + solver transparency for professional liability, (5) commercial/multi-story/complex adjacency beyond residential. Open-source AI is research-grade; commercial tools are closed and often expensive or limited in export fidelity.

### Recommended Architecture and Technology Stack

**Hybrid system** is superior to pure neural, pure procedural, or pure solver:

- **LLM layer** (for specification parsing and user interaction only): Parse natural-language + structured technical specs into a formal intermediate representation (rooms, target areas, adjacencies, materials, code constraints as structured JSON/graph). Use for dialogue, explanation, and option ranking. **Must not** control final geometry or compliance decisions (hallucination risk, non-determinism, liability).

- **Constraint solver + procedural core** (deterministic geometry): Graph-based room adjacency + area packing (inspired by Graph2Plan/HouseGAN++ post-processing) on a construction grid. Use integer programming / SAT / custom geometric solvers for hard constraints (areas, circulation, boundaries, egress). Post-process neural proposals if used.

- **Optional neural component**: Fine-tuned or pretrained layout model (HouseGAN++ style or diffusion) for diverse initial proposals, then strictly refined/validated by solver.

- **Geometry & export**: CadQuery or pure 2D geometry (Shapely + ezdxf) for walls/doors/windows as parametric entities. Layers, blocks, hatches, dimensions via ezdxf. Title blocks + annotations. Vector PDF via ReportLab (precise) or WeasyPrint (HTML templates with SVG). IFC export via IfcOpenShell (IfcWall, IfcSpace, IfcDoor, etc.).

- **Compliance**: Rule engine that maps extracted constraints to geometric queries. RAG for clause retrieval only; executable validators for pass/fail.

- **Stack**: Python (ezdxf, IfcOpenShell, CadQuery/OCP, Shapely, NetworkX or similar for graphs, PuLP/OR-Tools for solving, ReportLab/WeasyPrint). Optional Blender/Bonsai for visualization. Headless-first. Containerized for deployment.

Neural alone fails hard constraints and editability. Pure procedural lacks diversity. Pure solver can be brittle on complex programs. Hybrid gives diversity + guarantees.

### MVP Roadmap

1. Spec parser (LLM → formal graph + constraints) + simple rectangular packing solver.
2. ezdxf exporter (walls, rooms as hatches, doors/windows as blocks, basic dimensions, layers).
3. Vector PDF (title block + plan) via ReportLab.
4. Basic area/adjacency validation.
5. Multiple options generation + simple ranking.
6. IfcOpenShell export of spaces/walls.
7. Add neural proposal stage + iterative refinement.
8. RAG-assisted rule extraction + deterministic checkers.
9. Editable intermediate (JSON or OAS-like schema) for user overrides.

### Validation Strategy

- **Geometry**: Unit tests on known room counts/areas/adjacencies; topological validity (no overlaps, closed spaces); comparison to RPLAN-style ground truth where licensed.
- **Code compliance**: Golden set of compliant/non-compliant models per target code; coverage of extracted rules; independent geometric measurement.
- **DXF**: Round-trip load in AutoCAD/LibreCAD/ezdxf; layer/block/hatch/dimension fidelity; visual inspection.
- **PDF**: Vector integrity, print fidelity, dimension accuracy.
- **BIM**: IfcOpenShell validation + openBIM IDS checks; import into Bonsai/FreeCAD/Revit.

### Major Dead Zones

- Production-grade open multi-jurisdiction building-code rule engines with machine-readable formal semantics.
- Large, diverse, freely redistributable vector floor-plan datasets beyond RPLAN residential (especially commercial, multi-story, non-Chinese).
- Reliable neural models that truly enforce exact areas + circulation + code without heavy post-processing.
- Headless, high-fidelity 2D CAD annotation (auto-dimensions, smart title blocks) without custom engineering.
- Legal/ liability frameworks for AI-generated layouts used in permitting.

### Three Strongest Product Opportunities

1. **Open hybrid configurator** focused on residential + light commercial with guaranteed DXF/IFC + deterministic constraint solving (fill gap left by Maket-style tools that lack professional export fidelity and hard constraints).
2. **Code-aware feasibility engine** that ingests municipal PDFs via RAG, converts to executable constraints, and validates AI or user layouts against them (addresses ACC weakness across products).
3. **BIM-native early-design bridge**: Program → multiple IFC-ready options with live metrics and seamless Revit/Bonsai handoff, emphasizing editability and audit trails (differentiates from pure massing tools like Forma/TestFit).

This architecture prioritizes verifiability, professional interoperability, and liability-safe determinism while leveraging AI only where it adds value (parsing, diversity, interaction). All recommendations rest on the verified open-source foundations and observed market gaps above.