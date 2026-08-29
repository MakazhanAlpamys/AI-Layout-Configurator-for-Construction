# Building an AI Layout Configurator for Construction: Architecture, Technology Stack, and Market Analysis

## TL;DR
- **Build a hybrid system, not a pure neural one.** Use an LLM only for the "front door" (parsing the technical spec into a structured schema, natural-language editing, and retrieving code clauses) and a constraint/optimization solver (OR-Tools CP-SAT) as the geometry engine that HARD-enforces room areas, adjacencies, corridors, and the building footprint. Emit editable 2D drawings with ezdxf (DXF), a vector-PDF backend, and an IFC model via IfcOpenShell. Neural generators (HouseDiffusion, House-GAN++) are useful only as a "seed/inspiration" layer whose output must be re-solved, because none of them truly enforce hard constraints.
- **The mature, correctly-licensed open-source spine exists today:** ezdxf (MIT) for DXF, IfcOpenShell (LGPL-3.0) for IFC/IDS, buildingSMART IDS for machine-checkable data requirements, OR-Tools CP-SAT (Apache-2.0) for the solver, Shapely (BSD-3-Clause) for 2D geometry, and ReportLab/WeasyPrint (BSD) or the matplotlib PDF backend for vector PDF. LibreDWG (GPL-3.0) can write DWG but is beta and unreliable for round-tripping — treat DWG as export-only via ODA File Converter, not a core dependency.
- **The biggest dead zone is automated *code* compliance.** IDS checks *data/property* requirements (e.g., "every wall has a fire rating"), not geometric legality (egress distances, clearances). No open, machine-readable, jurisdiction-complete building code exists; deterministic geometric rules must be hand-coded and validated. LLMs must NEVER decide final compliance or emit final geometry.

## Key Findings

1. **Two classes of "generation" dominate and they are not interchangeable.** Solver/procedural methods (TestFit, Finch, ARCHITEChTURES, OR-Tools, GPLAN-style graph methods) *guarantee* constraints; neural methods (House-GAN++, HouseDiffusion, Graph2Plan) only *encourage* them and routinely produce duplicated/missing rooms, overlaps, and non-watertight geometry. The clearest confirmation is that TestFit's own "AI" is described by reviewers as "closer to mature constraint programming than to learned generative models."
2. **The commercial market is bifurcated:** developer-facing feasibility/massing (TestFit, Autodesk Forma) and residential floor-plan generation (Maket.ai, Finch, ARCHITEChTURES). Only a few (ARCHITEChTURES, Maket.ai, Finch Enterprise) export the trio a serious AEC workflow needs — DXF + IFC/BIM — and none combine strong hard-constraint solving, real machine-readable code compliance, and clean CAD/BIM output at once. That is the gap.
3. **IDS became an official buildingSMART standard in 2024** and is the correct backbone for the *data* half of compliance, with open tooling (IfcOpenShell's `ifctester`). Per buildingSMART, "On 1st June 2024 the IDS has been approved and became the official buildingSMART standard!" (with the standard formally approved at the London meeting in June 2024). The *geometric* half (egress, clearances, daylight) still requires bespoke deterministic checkers; research efforts (CODE-ACCORD, RASE, LegalRuleML, Singapore CORENET X) show the direction but none give you a ready-made, open, universal rule engine.
4. **Datasets are the hidden constraint on neural approaches.** RPLAN (request-only, single-apartment, Asian layouts) underpins most published models; MSD (2024, multi-apartment, CC BY 4.0 dataset) is more realistic but its own paper shows existing models "cannot yet seamlessly address" it. Pretrained weights for the marquee models are thin or unlicensed.

## Details

### AREA 1 — Open-source technologies (verified)

**DXF / DWG / PDF generation**

| Project | Purpose | License (SPDX) | Repo signal (verified Aug 2026) | Status | Key limits |
|---|---|---|---|---|---|
| **ezdxf** (mozman/ezdxf) | Create/read/modify DXF; render to PDF/SVG/PNG via matplotlib backend | **MIT** | ~1.3k★, 254 forks; latest docs updated May 2026; 193 tags | Active | DXF only (no DWG write). Round-trips R12–R2018; preserves 3rd-party tags. Supports lines, LWPOLYLINE, layers, blocks (INSERT), HATCH, DIMENSION, MTEXT — everything needed for walls/doors/windows/title blocks. |
| **LibreDWG** (GNU) | Read/write DWG | **GPL-3.0-or-later** | GNU project; stable 0.13.3 (Feb 2024) | Active but beta | Reliable read r13–r2018; write good only for r1.1–r2000; R2010+ write "CRC errors still." GPL is viral — a problem for closed products. |
| **libdxfrw / dxflib (QCAD)** | DWG/DXF read; simple DWG | GPL family | not independently re-verified | — | libdxfrw can read simple DWGs; dxflib is GPL/commercial dual. |
| **ReportLab** | Programmatic vector PDF (title blocks, sheets) | **BSD-3-Clause** (core toolkit) | PyPI OSI BSD classifier | Active | Core is BSD; the separate pyRXP/commercial "plus" components differ. |
| **WeasyPrint** (Kozea) | HTML/CSS → vector PDF | **BSD-3-Clause** | LICENSE confirmed on GitHub | Active | Great for report/sheet layout, not for CAD geometry. |
| **matplotlib PDF backend** | Vector PDF/SVG from plotted geometry | PSF/BSD-style | — | Active | ezdxf's `drawing` add-on targets it directly — simplest path from DXF entities to vector PDF. |

**Geometry kernels & CAD**

| Project | Purpose | License (SPDX) | Repo signal | Status | Limits |
|---|---|---|---|---|---|
| **Shapely** | 2D computational geometry (polygons, buffering, adjacency, area) | **BSD-3-Clause** | — | Active | Ideal for room polygons, corridor buffering, overlap/gap checks. Underlying GEOS is LGPL-2.1. |
| **Open CASCADE (OCCT)** | Industrial B-rep 3D kernel | **LGPL-2.1-only WITH OCCT-exception-1.0** | dev.opencascade.org | Active | Heavy; needed only for true 3D solids/BIM geometry. |
| **CadQuery** (CadQuery/cadquery) | Parametric CAD-as-code on OCCT | **Apache-2.0** | ~5.3k★, 501 forks; v2.7.0 released Feb 13 2026 | Active | 3D-part oriented, not floor-plan oriented; useful for 3D export (STEP). |
| **pythonOCC / OCP** | Python bindings to OCCT | LGPL family | — | Active | Bindings only. |
| **FreeCAD** (FreeCAD/FreeCAD) | Full parametric CAD app + Python | **LGPL-2.1** (GitHub-classified) | ~31.6k★, 5.7k forks; v1.1.1 released Apr 14 2026 | Active | Scriptable headless; heavyweight dependency. |
| **OpenSCAD** | Procedural solid modeling | GPL-2.0 | — | Active | CSG mindset; poor fit for architectural plans. |

**BIM / IFC**

| Project | Purpose | License (SPDX) | Repo signal | Status | Limits |
|---|---|---|---|---|---|
| **IfcOpenShell** (+ `ifctester`, `ifcopenshell-python`) | Read/write IFC2X3/IFC4/IFC4X3; geometry engine; IDS validation; BCF | **LGPL-3.0-or-later** (core; some tools GPL-3.0) | ~2.7k★, 944 forks; default branch v0.8.0 | Active | The reference open IFC toolkit. `ifctester` validates IFC against IDS. |
| **Bonsai** (formerly BlenderBIM) | Blender-based IFC authoring GUI | GPL-3.0 | part of IfcOpenShell org | Active | GUI/authoring, not headless service core. |
| **ThatOpen / web-ifc** (engine_web-ifc) | JS/WASM IFC read/write in browser/node | **MPL-2.0** | ~1.0k★, 279 forks; v0.70 (Jul 2026) | Active | Best for a web front-end viewer; MPL is file-level copyleft (usable in commercial). |
| **web-ifc-viewer / IFC.js** | Three.js BIM viewer toolkit | MPL-2.0 | — | Active | Superseded/reorganized under ThatOpen ("That Open Engine"). |
| **IfcPlusPlus** (ifcquery) | C++ IFC reader/writer + viewer | — | repo self-describes as "more or less archived" | **Stale/archived** | Author now recommends web-ifc instead. |
| **Xbim Toolkit** | .NET IFC toolkit | CDDL/open | not re-verified | Active | .NET ecosystem alternative. |

**Verdict (Area 1):** For a Python service, the spine is **ezdxf + Shapely + IfcOpenShell + (ReportLab or matplotlib PDF)**. DWG output should be handled by exporting DXF and converting with ODA File Converter (free) rather than depending on LibreDWG's fragile writer. All core pieces are permissively licensed (MIT/BSD/Apache/LGPL) — no GPL contamination if LibreDWG is avoided.

### AREA 2 — AI & algorithmic floor-plan generation

**Neural / learned methods**

| Project | I/O | Dataset | Code / weights | License | Hard constraints? |
|---|---|---|---|---|---|
| **House-GAN** / **House-GAN++** (ennauata) | Bubble-diagram graph → room bounding boxes/raster | RPLAN (~60k+) | Code yes; test.py runs a pretrained model | GitHub-unclassified LICENSE (SPDX **not found**) | **No.** Per HouseDiffusion supplementary (Shabani et al.): "the major issue of House-GAN++ is duplicate or missing rooms, ignoring the input constraint." |
| **HouseDiffusion** (aminshabani/house_diffusion) | Graph constraint → vector floorplan (polygon loops) | RPLAN | Code yes; dual "Unknown + GPL-3.0" | GPL-3.0 (one file) | **No — soft.** Diffusion denoises toward constraints; can control corner count, but areas/adjacency are encouraged, not guaranteed. Per Shabani et al. (CVPR 2023): "compared to the current state-of-the-art House-GAN++, HouseDiffusion makes an average improvement of 67% in diversity and 32% in compatibility" (author-claimed, on RPLAN). |
| **Graph2Plan** (Hu et al.) | Boundary + layout graph → boxes + raster → vectorized | RPLAN (~80k) | Code exists (academic) | academic | **No.** Retrieve-and-adjust; door connectivity heuristic. |
| **WallPlan / FloorplanGAN / iPLAN / MaskPlan** | Various graph/boundary → plan | RPLAN/LIFULL | mixed academic | mixed | **No.** All learned = soft constraints. |
| **LayoutGPT / Tell2Design / Architext** | Text → layout | text-paired sets | mixed | mixed | **No.** LLM/text-conditioned; geometry unreliable. |

**Datasets**

| Dataset | Content | Access / License |
|---|---|---|
| **RPLAN** | ~80k single-apartment Asian residential vector plans | **Request-only academic** (form). Downstream repos note download difficulties. |
| **MSD (Modified Swiss Dwellings)** | 5.3k+ plans, 18.9k+ apartments, multi-unit; image/vector/graph | Code repo (no license declared); **dataset CC BY 4.0** (4TU record). ECCV 2024. |
| **Swiss Dwellings v3.0.0** | Source of MSD | open |
| **CubiCasa5K** | 5k annotated plans (parsing) | CC BY-NC 4.0 |
| **FloorPlanCAD** | 15k+ CAD SVG floor plans, panoptic symbol spotting | CC BY-NC 4.0 |
| **Structured3D / ZInD / LIFULL HOME'S** | large-scale, varied | research terms; LIFULL via NII |
| **ResPlan (2025)** | 17k vector+graph residential plans | recent (arXiv 2508.14006) |

**Non-neural (constraint / procedural / optimization) — these DO enforce hard constraints**

- **OR-Tools CP-SAT** (google/or-tools, **Apache-2.0**, 13,960★/2,476 forks, v9.15 Jan 12 2026): encode rooms as rectangles with integer coordinates; hard constraints for non-overlap, area (via width×height or area lower bounds), footprint containment, adjacency (shared-wall length), and corridor connectivity. This is the recommended geometry engine.
- **Z3 (SMT), Gurobi/CBC/HiGHS (MIP):** alternatives; Z3 good for feasibility, MIP for objective optimization (compactness, wall-length).
- **GPLAN / G2PLAN (graph-theoretic + linear optimization):** generate *all* topologically distinct dimensioned floorplans satisfying adjacency + dimensional constraints in near-linear time; mathematically guarantees constraints (author-claimed "thousands of floorplans in a few milliseconds"). Rectangular/orthogonal boundaries.
- **Evolutionary / metaheuristic:** Galapagos & Wallacei (Grasshopper), DEAP, pymoo (all open) for multi-objective layout search; simulated annealing for packing. These optimize but need explicit feasibility constraints or repair.
- **Rectangular dissection / K-D tree / squarified treemap / Voronoi growth:** fast procedural subdivision; good for seeding, weak on complex adjacency.

**Verdict (Area 2):** Hard constraints (areas, adjacency, corridors, footprint) are *only* truly enforced by solver/graph/procedural methods. Neural models are best used to propose *topologies/seeds* that are then re-solved. This is the single most important architectural decision in the whole system.

### AREA 3 — Building-code compliance, BIM, ACC, and RAG

**What is real and open today**
- **IDS (Information Delivery Specification), buildingSMART — official standard since 2024 (v1.0).** XML, strictly tied to IFC. Checks that objects/properties/values exist and fall in ranges (e.g., "all walls have Pset_WallCommon.FireRating ∈ {REI30, REI60, REI90}"). Open validators: IfcOpenShell `ifctester` (HTML/BCF reports), plus vendor tools (Solibri rule #244, BIMcollab, ACCA usBIM.IDS). **Crucial limit: IDS validates data/attributes, not geometric legality.**
- **bSDD (buildingSMART Data Dictionary):** shared classifications/properties to anchor IDS.
- **mvdXML:** older model-view-definition rule format, largely superseded by IDS for information checking.
- **Research / national efforts:** Singapore **CORENET X** (soft-launched Dec 2023) uses IFC+SG and BCA's automated code-checking engine — the world's most advanced live ACC deployment. Per BCA's official support portal, "From 1 October 2026, submission via CORENET X Gateway Processes will be mandatory for all new projects" (mandatory for all new building projects regardless of GFA from that date, up from the ≥30,000 m² threshold in effect since October 2025) — but it is jurisdiction-specific and partly proprietary (Solibri engine, AcePLP checker). **CODE-ACCORD** (Nature Sci Data 2024): 862 annotated sentences from England+Finland regs, 4,297 entities / 4,329 relations, for NLP rule extraction. **RASE** (Requirement/Applicability/Selection/Exception tagging), **LegalRuleML** (XML deontic logic), **SMARTcodes/AEC3**, **KBimLogic** — all research/local; none internationally adopted as open machine-readable code. **ACCORD / CHEK** EU projects push the agenda.
- **Rule engines available:** Drools, CLIPS, Jena rules, Datalog, SHACL/OWL, Z3 — all usable to *execute* deterministic rules once encoded.

**How rules should be converted and where LLMs must NOT be trusted**
- Correct pipeline: (1) LLM/NLP *assists* extraction of candidate rules from regulatory text (RASE-style tagging, entity/relation extraction) → (2) a human domain expert reviews and encodes each rule as a **deterministic, testable predicate** (IDS for data; hand-written geometric checkers for egress distance, corridor width, door clearance, daylight/area ratios) → (3) the deterministic checker runs against the geometry/IFC and produces a pass/fail with the exact clause cited → (4) results are validated against a labelled test suite.
- **LLMs must NOT:** decide final pass/fail on compliance; generate or edit final geometry; be the sole interpreter of a code clause; or "hallucinate" a numeric threshold. RAG-over-code can *retrieve and summarize* the relevant clause for a human and *draft* a candidate rule, but the operative check must be deterministic and auditable. Studies (e.g., LLM interpretation of building regs, arXiv 2407.21060) confirm LLMs are useful for interpretation support, not authoritative compliance.

### AREA 4 — Existing products and market gaps (proprietary — separated from open source)

| Product | Inputs | Outputs | DXF/IFC | Constraints | Pricing (source) | Users / notes |
|---|---|---|---|---|---|---|
| **TestFit** | Site/parcel, program, zoning setbacks, FAR, parking ratios, unit mix | Massing + site layout + pro forma; exports to Revit/AutoCAD/SketchUp | DXF/CAD export yes; IFC not emphasized | **Hard (constraint solver)** — reviewers call it "constraint programming, not learned generative" | Site Solver + generative are paid subscription; Site Solver/Enterprise pricing is sales-gated (vendor pricing page) | Dallas, founded 2016; raised $20M Series A led by Parkway Venture Capital (with Prologis Ventures, Moderne Ventures, Perot Jain, Schematic Ventures), total $22M to date (GlobeNewswire, 26 Jul 2022). Deterministic takeoffs. Feasibility stage only. |
| **Autodesk Forma** (ex-Spacemaker) | Site, massing, envelope, setbacks | Generative site/massing options; sun/wind/noise/embodied-carbon analysis | Revit integration; limited plan-level CAD | Constraint + analysis driven; some AI features non-ML | Subscription (Autodesk); trial available | Autodesk acquired Oslo-based Spacemaker AS for ~$240M net of cash, closing 23 Nov 2020 (Autodesk SEC Form 10-Q); rebranded Forma 2023, now "Forma Site Design." Early-stage, not detailed floor plans. |
| **Maket.ai** | Room types/counts, target sqft, lot shape, adjacencies; natural language | Multiple residential plans; renders; DXF + PDF export; regulatory Q&A on uploaded zoning PDF | **DXF + PDF** (paid tiers); no IFC | Adjacency/clearance surfaced in editor; "zoning" via LLM Q&A (not deterministic geometric ACC) | Free 50 credits; consumer plan $20/mo for 300 credits; enterprise $1,200/mo (vendor pricing page; BetaKit) | Founded Montreal 2020, publicly launched 2023; "over 1 million registered users and grown to a team of 14 employees since it officially launched in 2023" (BetaKit). Residential draft engine, "not an architect." |
| **Finch** (Finch3D) | Massing/envelope + graph rules + firm "Plan Library" of code/accessibility rules | Optimized floor plans, unit layouts, metrics; BIM export on Enterprise; Revit/Rhino/Grasshopper/Forma integration | IFC/BIM on Enterprise; Revit | **Graph-based rule engine + optimization** (hard-ish, rule-driven) | Free tier; Basic (AI limited to multifamily residential); Enterprise sales-gated | Malmö, Sweden; spun out of Wallgren Arkitekter. |
| **ARCHITEChTURES** | Room sizes/dimensions, urban parameters, regulatory params | Optimized residential BIM + project data; **exports XLSX, DXF, IFC** | **DXF + IFC** | AI optimizes to user parameters; urban-param compliance | Pro $40/mo (billed annually ≈ €41/mo / $480–€492/yr) (vendor & review sources) | Multifamily residential; 170+ countries claimed (vendor). |
| **Snaptrude** | Browser BIM modeling | BIM, exports to Revit/IFC | IFC/Revit | manual + AI assist | Free tier + paid (vendor) | Browser BIM for architects. |
| **Planner5D / Hypar / Giraffe / Digital Blue Foam / Arcol / Motif / Qbiq / Rayon / Modelur / cove.tool / Higharc / Swapp / Skema / Augmenta / laiout** | varied | varied | varied | Hypar & Giraffe = computational/parametric platforms; Qbiq/laiout = interior test-fit; Modelur/cove.tool = urban/performance | varied | Hypar targets computational designers (functions/code-driven); Qbiq automates interior layouts + 3D tours; laiout does floor-plate test-fit with live metrics. |

**Market gap (the opportunity):** No product combines (a) rigorous **hard-constraint solving** for areas/adjacency/corridors/footprint, (b) **deterministic, auditable code compliance** (not LLM Q&A), and (c) **clean simultaneous DXF + vector-PDF + IFC** output, driven by (d) a **natural-language spec front door**. TestFit nails (a) for site feasibility but not detailed rooms; Maket nails (d) but is a draft engine with soft compliance; ARCHITEChTURES/Finch get closest on outputs but compliance is parameter/graph-based, not code-clause-deterministic.

---

## Recommendation: architecture & technology stack

**Guiding principle — separation of concerns by trust level:**
- **Probabilistic layer (LLM, low trust):** understands humans, proposes, retrieves. Never authoritative.
- **Deterministic layer (solver + geometry + checkers, high trust):** owns all geometry and all compliance verdicts. Fully auditable and testable.

**Component diagram (data flow):**
1. **Spec Intake & Parsing (LLM):** natural-language / document spec → structured JSON schema (dimensions, floors, rooms, target areas, adjacency graph, materials, code references). LLM extracts; a **schema validator** (JSON Schema / Pydantic) rejects malformed output. Human confirms.
2. **Constraint Compiler:** JSON → CP-SAT model. Rooms = integer-coordinate rectangles (or rectilinear unions); constraints = footprint containment, non-overlap, per-room area bounds, adjacency (shared-edge length ≥ threshold), corridor/circulation connectivity, code-derived minimums (corridor width, door clearance).
3. **Solver (OR-Tools CP-SAT):** produces multiple *feasible* layouts; objective ranks them (compactness, wall length, daylight proxy). **Optional seed:** a neural model (HouseDiffusion/House-GAN++) or GPLAN proposes topologies that are fed in as hints and then *re-solved* so the output is guaranteed feasible.
4. **Geometry builder (Shapely):** solver output → room polygons → wall centerlines/thicknesses → door/window openings → corridors. Validate watertightness, no gaps/overlaps.
5. **Compliance engine (deterministic):** two tracks — (a) **IDS** (`ifctester`) for data/property requirements on the IFC; (b) **hand-coded geometric checkers** for egress distance, clearances, min room areas/dimensions, daylight ratios. Each verdict cites the clause. RAG-over-code (LLM) only *retrieves/explains* clauses to the user and *drafts* new candidate rules for human encoding.
6. **Export layer:** **ezdxf** → DXF (layers per WALL/DOOR/WINDOW/DIM/TEXT, blocks for doors/windows/title block, hatches, dimensions); **matplotlib PDF backend or ReportLab/WeasyPrint** → vector PDF sheets with title block; **IfcOpenShell** → IFC4/IFC4x3 (IfcWall, IfcDoor, IfcWindow, IfcSpace) for BIM round-trip. DWG via ODA File Converter if required.
7. **Interactive edit loop:** user edits in NL ("widen the corridor to 1.5 m") → LLM maps to constraint changes → re-solve → re-export. Geometry never edited directly by the LLM.

**Why this stack:** every geometry- and compliance-critical component is deterministic, testable, and permissively licensed; the LLM is confined to parsing, interaction, and retrieval where errors are recoverable and human-checked.

## MVP roadmap (phased)

- **Phase 0 — Spine (build first):** JSON spec schema + CP-SAT rectangular-room solver for a single rectangular footprint, single floor; Shapely geometry; ezdxf DXF export with layers/blocks/dimensions; matplotlib vector PDF. Deliverable: type a small program (rooms, areas, adjacencies) → get N feasible DXF+PDF layouts. This proves hard-constraint enforcement end to end.
- **Phase 1 — Language front door:** LLM spec parsing with schema validation + human confirmation; NL edit loop that maps to constraint deltas and re-solves.
- **Phase 2 — BIM + data compliance:** IfcOpenShell IFC4 export (walls/doors/windows/spaces); author an IDS and validate with `ifctester`; title-block/sheet generation.
- **Phase 3 — Geometric code checks:** deterministic checkers for corridor width, egress travel distance, door clearances, min room dimensions; clause citation; RAG retrieval of clauses for the user.
- **Phase 4 — Multi-floor, non-rectangular footprints, seeding:** rectilinear rooms, vertical circulation alignment, optional neural seeding re-solved by CP-SAT; DWG export via ODA.
- **Defer:** free-form/curved geometry, full 3D solids, MEP, structural, jurisdiction-complete code libraries, photorealistic rendering.

## Validation strategy
- **Geometry:** automated Shapely assertions — every room polygon valid & simple; union of rooms ⊆ footprint; pairwise intersection area = 0 (no overlaps); no unintended gaps; sum of room areas within tolerance of targets; adjacency graph of output equals requested graph. Golden-file regression tests.
- **Code compliance:** a labelled test suite of known-compliant and known-violating layouts; every deterministic checker must correctly classify all fixtures (no false negatives on hard-safety rules like egress). IDS validated with `ifctester` producing BCF; assert expected pass/fail counts.
- **DXF output:** round-trip test — write with ezdxf, re-read, assert entity counts/layers/blocks/dimensions preserved; open in a headless converter (ODA/QCAD) to confirm no corruption; verify layer naming convention.
- **PDF output:** assert vector (not raster) content, correct scale, title-block fields populated, dimensions legible; visual-diff against golden PDFs.
- **IFC output:** validate against IFC schema (IfcOpenShell), IDS validation, and re-import into a second tool (web-ifc / FreeCAD) to confirm walls/doors/windows/spaces survive round-trip.

## Major DEAD ZONES (things that don't exist / don't work / are harder than they look)
1. **A universal, open, machine-readable building code.** Does not exist. IDS checks data, not geometric legality. Every geometric rule is bespoke per jurisdiction and must be hand-coded and maintained. CORENET X shows it's possible only with massive national effort and partly proprietary engines.
2. **Neural models that honor hard constraints.** None do. House-GAN++/HouseDiffusion/Graph2Plan produce plausible but non-guaranteed layouts (duplicate/missing rooms, overlaps, non-watertight). They cannot be trusted to hit exact areas or footprint.
3. **Reliable open-source DWG *writing*.** LibreDWG writes only up to r2000 reliably; R2010+ has CRC errors. Treat DWG as convert-on-export, not native.
4. **Pretrained weights you can ship.** Marquee floor-plan models have thin/unclassified licenses (House-GAN++ unclassified, HouseDiffusion GPL/unknown) and depend on request-only RPLAN — legally and practically fragile for a commercial product.
5. **LLM "zoning compliance."** Products marketing LLM zoning Q&A (e.g., Maket's regulatory assistant) are informational, not deterministic verification. Do not conflate.
6. **Round-trip editability of AI raster plans.** Raster/pixel outputs must be vectorized (lossy). Generate vector-native from the solver instead.

## The three strongest product opportunities
1. **"Feasible-by-construction" residential/multifamily configurator:** LLM spec intake + CP-SAT hard-constraint solver + simultaneous DXF/PDF/IFC export. Beats Maket (soft, no IFC) and matches ARCHITEChTURES/Finch on outputs while guaranteeing constraints and offering auditable checks. Clear white space.
2. **Deterministic code-compliance layer as a product/API:** IDS authoring + geometric checkers + clause-cited reports over IFC, with RAG retrieval to help users, targeting jurisdictions moving to digital submission (Singapore CORENET X now; UK, EU via ACCORD/CHEK next). Sell the compliance engine even to competitors.
3. **Interior/tenant test-fit engine for CRE:** constrained space-planning of an existing floor plate (like Qbiq/laiout) with hard adjacency/area/egress constraints and instant DXF+IFC, plugged into leasing workflows — a bounded, high-value, technically tractable slice that avoids the hardest full-building-code problems.

## Caveats
- GitHub star/fork counts are rounded values read from repo headers on/around Aug 28–29, 2026 (OR-Tools exact 13,960★/2,476 forks from the org listing). **Last-commit dates could not be verified** from rendered repo pages; where a dated release appeared it is cited (CadQuery 2.7.0 Feb 13 2026; FreeCAD 1.1.1 Apr 14 2026; web-ifc v0.70 Jul 2026; OR-Tools v9.15 Jan 12 2026 via release tag).
- Several key repos have **no clean SPDX license**: House-GAN++ (unclassified), HouseDiffusion (dual "Unknown + GPL-3.0"), buildingSMART/IDS (no SPDX shown on page), MSD code repo (no license; the *dataset* is CC BY 4.0). Verify before any commercial use.
- All performance/adoption numbers for commercial products (TestFit users/deals, Maket 1M+ users, ARCHITEChTURES 170+ countries, HouseDiffusion +67%/+32%) are **vendor- or author-claimed**, not independently measured.
- Pricing changes frequently and enterprise tiers are sales-gated; figures cited are from vendor pricing/review pages as of 2026.
- A few libraries mentioned in the brief (dxfgrabber, svglib, Cairo, Skia, Speckle, Teigha/ODA SDK) were not individually re-verified within the research budget; they are noted as candidates but their status here is **unverified**.