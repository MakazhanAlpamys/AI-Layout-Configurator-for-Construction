# AI Layout Configurator for Construction — Deep Research Report

## Executive Summary

Building an AI Layout Configurator that ingests a technical specification — building dimensions, floors, rooms, target areas, adjacencies, materials, and building-code constraints — and outputs several architectural layout options as editable DXF and vector PDF is technically feasible today only as a hybrid system. Purely neural approaches excel at inspiration and graph-conditioned layout diversity but fail to enforce hard geometric, area, and regulatory constraints. Purely procedural solvers guarantee constraints but produce sterile, non-human layouts without learning priors. The viable architecture separates concerns: LLM for spec parsing and interaction, graph-neural or diffusion model for coarse topology proposals, deterministic constraint solver (Google OR-Tools CP-SAT) for final geometry, and verified open-source CAD/BIM kernels for export.

Open-source CAD foundations are mature: ezdxf provides full MIT-licensed DXF R12-R2018 read/write including layers, blocks, hatches, dimensions, and title blocks [[1]](https://github.com/mozman/ezdxf); Open CASCADE (LGPL-2.1-only) and its Python bindings pythonocc-core (LGPL-3.0) provide B-Rep kernel [[2]](https://github.com/tpaviot/pythonocc-core); IfcOpenShell (LGPL-3.0-or-later) is the de facto IFC toolkit including ifcclash and ifctester IDS validation [[3]](https://github.com/IfcOpenShell/IfcOpenShell). For vector PDF, ReportLab BSD-3-Clause and WeasyPrint BSD-3-Clause are production proven [[4]](https://github.com/anacondarecipes/reportlab-feedstock) [[5]](https://github.com/Kozea/WeasyPrint).

On the AI side, the lineage from RPLAN (80k+ floorplans) [[6]](http://staff.ustc.edu.cn/~fuxm/projects/DeepLayout/index.html) to HouseGAN (ECCV 2020, arXiv:2003.06988) [[7]](https://arxiv.org/pdf/2003.06988), HouseGAN++ (CVPR 2021, arXiv:2103.02574) [[8]](https://arxiv.org/abs/2103.02574), Graph2Plan (SIGGRAPH 2020, arXiv:2004.13204) [[9]](https://arxiv.org/abs/2004.13204), HouseDiffusion (CVPR 2023, arXiv:2211.13287) [[10]](https://arxiv.org/pdf/2211.13287v1), and MaskPLAN (CVPR 2024) [[11]](https://github.com/HangZhangZ/MaskPLAN) shows a shift from GAN raster masks to vector polygonal loops with Transformer and diffusion backbones. All support adjacency/bubble-diagram constraints, none natively support metric target areas, building boundaries with non-Manhattan geometry, or building-code rules without post-processing.

Regulatory compliance cannot be delegated to LLMs. buildingSMART IDS (Information Delivery Specification) [[12]](https://github.com/buildingSMART/IDS) is the computer-interpretable standard for IFC requirement checking, implemented by ifctester. RAG over codes is emerging but remains guidance-only; deterministic rule engines like BIMserver (AGPL-3.0) [[13]](https://github.com/opensourceBIM/BIMserver) and Solibri API (proprietary) remain the audit layer.

Proprietary products cluster in two niches: residential inspiration with DXF export (Maket.ai, $20-30/mo, DXF/PDF, no IFC) [[14]](https://www.maket.ai/pricing) and early-stage massing/feasibility for multifamily (TestFit ~$3k-5k/seat/year, Revit/IFC, no hard area constraints) [[15]](https://archigenai.com/testfit-feasibility-generative-design-review-2026.html), Finch3D (€49+/mo, graph-rule based, Revit/Rhino/Grasshopper) [[16]](https://dataconomy.com/tools/finch-3d/), Autodesk Forma ($185/mo standalone) [[17]](https://illustrarch.com/articles/design-softwares/73363-autodesk-forma-review.html), Snaptrude (~$500+/yr, AI floorplan to BIM LOD 300-350, IFC/Revit) [[18]](https://www.snaptrude.com/pricing). No product offers verified hard-constraint solving for target areas + adjacency + circulation + code compliance in one pipeline with editable DXF/PDF/IFC round-tripping — this is the market gap.

## 1. Open-source technologies

### 1.1 DXF/DWG/PDF generation

| Project | Official link | Purpose | Stack | License SPDX | Stars/Forks/Last commit | Downloadable | Performance | Maintenance | Limitations |
|---|---|---|---|---|---|---|---:|---|---|
| ezdxf | https://github.com/mozman/ezdxf | DXF R12-R2018 read/write, creation, manipulation | Python, C-ext optional | MIT | ~ unverified (maintained, release 1.4.x) | pip install ezdxf | vendor-claimed: handles >5GB via iterdxf add-on | Active, weekly commits | No native DWG write; needs ODA File Converter add-on odafc for DWG; does support walls via LINE/LWPOLYLINE, doors/windows as BLOCK inserts, layers, blocks, hatches, DIMENSIONS, title blocks, vector PDF via drawing add-on to matplotlib/PyQt [[19]](https://github.com/mozman/ezdxf) |
| LibreDWG | https://github.com/LibreDWG/libredwg | DWG R13-R2021 read/write | C, Python bindings | GPL-3.0-only | unverified | source build | independently measured slow on large files | Sporadic, legal risk due to DWG spec | No stable Pythonic API for architectural semantics |
| FreeCAD | https://github.com/FreeCAD/FreeCAD | Parametric CAD, DXF/DWG import/export | C++, Python, Open CASCADE | LGPL-2.1-or-later | >20k stars (unverified) | installer from freecad.org | vendor-claimed | Active | Heavyweight for headless; DXF export loses BIM semantics |
| ReportLab | https://www.reportlab.com/opensource/ | Vector PDF generation, Platypus layout | Python | BSD-3-Clause | ~ unverified | pip install reportlab | vendor-claimed | Active 30+ years | Low-level, requires manual layout for architectural drawings [[20]](https://github.com/anacondarecipes/reportlab-feedstock) |
| WeasyPrint | https://github.com/Kozea/WeasyPrint | HTML/CSS to PDF | Python | BSD-3-Clause | ~ unverified | pip | vendor-claimed | Active | Better for report PDFs than CAD drawings [[21]](https://github.com/Kozea/WeasyPrint) |

Verification: ezdxf supports layers via `doc.layers.add`, blocks, hatches, dimensions, and title blocks as modelspace/paperpsace entities. Round-tripping preserves third-party tags. DXF R2018 write confirmed in docs. DWG remains proprietary; LibreDWG GPL-3.0 restricts commercial use; ODA File Converter is proprietary but free.

### 1.2 CAD and geometry kernels

| Project | Link | Purpose | Stack | License | Maintenance | Notes |
|---|---|---|---|---|---|---|
| Open CASCADE Technology (OCCT) | https://dev.opencascade.org/doc/overview/html/index.html | B-Rep kernel | C++ | LGPL-2.1-only WITH OCCT exception [[22]](https://github.com/OPEN-CASCADE-SAS/OCCT) | Active, quarterly releases | Basis for FreeCAD, IfcOpenShell geometry |
| pythonocc-core | https://github.com/tpaviot/pythonocc-core | Python wrapper for OCCT | Python, SWIG | LGPL-3.0 [[23]](https://github.com/tpaviot/pythonocc-core) | Active | Enables programmatic wall solids, boolean ops |
| CadQuery | https://github.com/CadQuery/cadquery | Parametric CAD DSL on OCCT | Python | Apache-2.0 | Active | Good for procedural generation of building mass |
| Topologic | https://github.com/waterloo-works/topologic | Non-manifold topology for AEC | C++, Python topologicpy | AGPL-3.0 [[24]](https://dev.opencascade.org/project/topologic) | Active | Supports adjacency graphs, ideal for room adjacency constraints |
| OpenCascade + pythonocc + Topologicpy allows construction of space adjacency dual graphs required for solver. |

### 1.3 BIM/IFC tools

| Project | Link | Purpose | License | Maintenance | Capability |
|---|---|---|---|---|---|
| IfcOpenShell | https://github.com/IfcOpenShell/IfcOpenShell | IFC library, geometry engine, ifcclash, ifctester, ifcconvert | LGPL-3.0-or-later [[25]](https://github.com/IfcOpenShell/IfcOpenShell) | Very active | ifcclash clash detection [[26]](https://docs.ifcopenshell.org/ifcclash.html), ifctester IDS validation [[27]](https://docs.ifcopenshell.org/ifctester.html), ifcbimtester Gherkin BDD tests |
| Bonsai / BlenderBIM | https://github.com/IfcOpenShell/bonsai | BIM authoring on Blender | LGPL-3.0-or-later | Active | UI for IFC, but headless possible via bpy |
| IfcTester | part of IfcOpenShell, pip install ifctester | IDS XML validation | LGPL-3.0 | Active | CLI `ifctester ids.ifc model.ifc`, Python API `ids.Ids`, reporter Html/Bcf |
| BIMserver | https://github.com/opensourceBIM/BIMserver | IFC model server, versioning, mvdXML checking | AGPL-3.0 [[28]](https://github.com/opensourceBIM/BIMserver) | Moderate, 1728 stars July 2026 | Provides rule checking via mvdXML, BCF reporting; heavyweight Java |
| bSDD | https://github.com/buildingSMART/bsdd + https://search.bsdd.buildingsmart.org/ | Data Dictionary | MIT for client libs (bsdd PyPI) | Active | Standardizes material/property definitions; API access |

All IFC tools support walls (IfcWall), doors (IfcDoor), windows (IfcWindow), layers via IfcPresentationLayerAssignment, blocks via IfcTypeProduct. Round-tripping IFC -> DXF loses parametric constraints; keep IFC as source of truth.

### 1.4 Headless rendering

- ezdxf drawing add-on + matplotlib to PNG/SVG/PDF: vector, headless, supports all entities.
- IfcOpenShell + pythonocc for 2D projection: generate plan sections.
- Three.js + web-ifc for web preview (MIT).

## 2. AI and algorithmic floor-plan generation

### 2.1 Taxonomy

GAN (HouseGAN), GAN-refinement (HouseGAN++), GNN+retrieval (Graph2Plan), Diffusion+Transformer (HouseDiffusion), Masked Autoencoder (MaskPLAN), Procedural + CP-SAT (PlanForge example), Constraint programming.

### 2.2 Key projects

| Project | Paper arXiv | Official code | Purpose | Stack | License | Dataset | Input / Output | Hard constraints | Weights | Status | Limitation |
|---|---|---|---|---|---|---|---|---|---|---|---|
| RPLAN | Wu et al 2019, http://staff.ustc.edu.cn/~fuxm/projects/DeepLayout/index.html | rplanpy toolbox https://github.com/oltremind/rplanpy | Large dataset 80k+ annotated residential floorplans PNG | Python | Research-only, license unverified | RPLAN 80k | PNG + vector | n/a | Download form required | Active reference | Residential only, Manhattan, no code |
| HouseGAN | arXiv:2003.06988 [[30]](https://arxiv.org/pdf/2003.06988) | https://github.com/ennauata/housegan | Relational GAN for bubble-diagram to layout | PyTorch | MIT? repo has no LICENSE file, community forks claim MIT but unverified; search result notes GPL-3.0 notice in vendor folder [[31]](https://github.com/creative-graphic-design/design-generators/issues/17) | LIFULL 117k vectorized | Input: bubble diagram graph (room types+adjacencies). Output: raster masks per room, bounding boxes | Supports adjacency, room count, but not target area, boundary, circulation, code | Dropbox model link, requires manual download | Last commit 2021, maintenance low | Raster output needs vectorization; fails on non-Manhattan |
| HouseGAN++ | arXiv:2103.02574 [[32]](https://arxiv.org/pdf/2103.02574) | https://github.com/ennauata/houseganpp [[33]](https://github.com/ennauata/houseganpp) | Layout refinement network | PyTorch | No LICENSE file, same as HouseGAN, unverified | RPLAN 60k | Input: bubble diagram + initial layout. Output: refined polygons | Adds compatibility metric, still no area/boundary hard enforcement | Demo houseganpp.com, model via Dropbox | Last commit 2021 | Same raster limitations, improved realism |
| Graph2Plan | arXiv:2004.13204 [[34]](https://arxiv.org/abs/2004.13204) | https://github.com/zzilch/graph2plan [[35]](https://github.com/zzilch/graph2plan) | Learning floorplan from layout graphs + boundary | PyTorch, GNN, Django interface | No LICENSE, unverified | RPLAN 80k, pre-processed Data.zip | Input: building boundary polygon + layout graph + door positions. Output: raster floorplan + bounding boxes, needs Matlab post-proc | Supports boundary, adjacency, room counts; target area weakly via retrieval; no code compliance | Data.zip GitHub release | Last commit 2020, low maintenance | Requires Matlab Python API, post-processing box alignment fails overlaps |
| HouseDiffusion | arXiv:2211.13287 [[36]](https://arxiv.org/pdf/2211.13287v1) | https://github.com/aminshabani/house_diffusion [[37]](https://github.com/aminshabani/house_diffusion) | Vector floorplan via diffusion with discrete+continuous denoising, Transformer core | PyTorch, guided-diffusion | MIT? LICENSE file states MIT in repo (verified by file viewer) | RPLAN, plus MagicPlan (AR real-world) mentioned as alternative [[38]](https://github.com/aminshabani/house_diffusion) | Input: bubble diagram. Output: vector polygonal loops rooms+doors (corners). | Supports exact corner count per room, parallelism/orthogonality/corner-sharing via denoising; adjacency via attention masks; still no hard area/boundary, but better than GAN | Google Drive ckpt exp/model250000.pt | Active 2023, CVPR 2023, maintenance moderate | Boundary-conditioned extension exists (Boundary-Constrained Diffusion Models arXiv:2602.01949) [[39]](https://arxiv.org/pdf/2602.01949v1) but not in main repo |
| FloorDiffusion | https://doi.org/10.1016/j.softx 2024 | https://github.com/JongHwa-Shim/FloorDiffusion | Conditional floorplan image generation via fine-tuned diffusion + inpainting | PyTorch, Stable Diffusion | MIT (per repo) | Custom | Input: conditional image (boundary) | Output: raster image | Supports boundary image condition | Code available | Recent 2024 | Raster only |
| MaskPLAN | CVPR 2024 [[40]](https://github.com/HangZhangZ/MaskPLAN) | https://github.com/HangZhangZ/MaskPLAN | Masked generative layout planning from partial input, Graph-structured Masked Autoencoder | PyTorch, Transformer x5 (T,L,S,A,R) + boundary image + front door | MIT (repo) | RPLAN | Input: partial attributes (any subset of type, location, size, adjacency, region, boundary, door). Output: vector boxes + region | Supports partial constraints, closest to target area via Size attribute; still soft, no code | Code + pretrained | Active 2024 | Max 8 rooms in original VLSI adaptation discussion, but architecturally up to ~12 |
| Tell2Design | ACL 2023 arXiv:2311.15941 | https://github.com/LengSicong/Tell2Design + spatialxia/Tell2Floorplan-dataset | Language-guided floorplan, 80k+ designs + NL instructions (5k human + 75k artificial) [[41]](https://arxiv.org/pdf/2311.15941v1) | PyTorch, LLM | MIT | T2D 80k (RPLAN enriched) | Input: natural language. Output: floorplan image | Language is soft constraint only | Dataset HuggingFace | Active 2023 | No hard metric guarantee |
| CubiCasa5K | Kalervo et al 2019 | https://github.com/schulzdaniel/cubicasa5k | Segmentation dataset 5000 samples 80 categories polygon annotations | PyTorch | MIT [[42]](https://github.com/schulzdaniel/cubicasa5k) | Zenodo 2613548 | Input: raster plan image. Output: segmentation | Not generation, but useful for plan parsing | Download Zenodo | Active | For analysis, not generation |
| FloorPlanCAD | https://floorplancad.github.io/ | https://floorplancad.github.io/ dataset site | 15,663 CAD drawings, 35 classes, SVG + PNG | Python | CC-BY-NC-4.0 for annotations [[43]](https://floorplancad.github.io/) | Official cloud drive 11,602 SVG verified | Input: CAD image. Output: panoptic symbols | Not generation | Dataset | Moderate | Non-commercial |
| PlanForge (example of CP-SAT approach) | https://github.com/karthiknitt/planforge | Indian G+1 generator, OR-Tools CP-SAT, Vastu + bye-laws | Python OR-Tools | MIT (per repo) | Synthetic + municipal rules | Input: plot dims + room prefs. Output: 3 scored layouts PDF/DXF/BOQ | Supports hard area, setback, municipal bye-laws | Code available | Active 2025 | India-specific rules, not general |

**Synthesis:** All neural models train on RPLAN (residential, mostly rectangular Chinese apartments) and thus generalize poorly to complex commercial or multi-floor buildings. Hard constraints like target area ±5%, adjacency must, circulation width, building boundary exact, and code are not truly enforced in any open model; they are soft via loss or attention. Performance numbers in papers (FID, GED, compatibility) are vendor-claimed, measured on RPLAN test split, not independent.

### 2.3 Solver-based and procedural

- Google OR-Tools CP-SAT: proven for zone placement (poolpet/floorplan4 Polish WT 2002 code [[44]](https://github.com/poolpet/floorplan4), karthiknitt/planforge). Model rooms as rectangles, constraints: non-overlap, adjacency via shared edge, area bounds, aspect ratio, circulation graph. Globally optimal.
- Topologic + pythonocc for non-rectangular: boolean partitioning.
- Houdini/Grasshopper not open but conceptually procedural.

Conclusion: Hybrid neural proposal + CP-SAT refinement is required.

## 3. Building-code compliance, BIM, ACC, and RAG

### 3.1 Open-source rule engines

- **IfcTester + IDS**: buildingSMART IDS standard XML schema ids.xsd [[45]](https://github.com/buildingSMART/IDS). Toolkit IDS.py from GSoC 2021 fully supports IDS authoring and validation [[46]](http://blog.ifcopenshell.org/2021/08/idspy-toolkit-as-result-of-google.html). Python usage: `ids.Ids(title=...)`, `ifctester.open(ids_path)`, `reporter.Console/Bcf/Html`. 250+ test file pairs. License: buildingSMART IDS is open standard, implementation LGPL.
- **IfcOpenShell BIMTester**: Gherkin BDD `ifcbimtester` wrapper for unit testing IFC models [[47]](https://github.com/IfcOpenShell/IfcOpenShell). Allows `Given an IfcWall ... Then ...`.
- **BIMserver**: Java, AGPL-3.0, supports mvdXML rule format and BCF reporting. Provides low-level API for checking. Used in AECOM research for fire exit, accessible toilet checks.
- **Solibri**: proprietary, but exposes Java API for rule authoring (Checking API) [[48]](https://solibri.github.io/Developer-Platform/latest/getting-started.html). Not open-source, expensive, but industry standard for code compliance; RAG could map to its rule docs.
- **ACC (Autodesk Construction Cloud) API**: Model Properties, Model Coordination, clash detection via APS Data Management API [[49]](https://forge.autodesk.com/blog/new-api-type-autodesk-construction-cloud). Enables upload IFC/RVT, query properties, trigger clash. Not open, requires APS token. Useful for enterprise integration, not for deterministic compliance logic.

### 3.2 Machine-readable building rules

- No universal open corpus. Examples: Dutch building decree Chapter 5 MPG calc plugin for BIMserver https://github.com/asbaharoon/nl-mpg-calc (AGPL), Philippine codes YAML https://github.com/aiinterruptor/ph-building-codes (MIT). Must be curated per jurisdiction.
- Approach: Convert regulation text into deterministic constraints via LegalRuleML or SHACL (as in RegRAG-IFC benchmark UK Approved Doc M) [[50]](https://github.com/ysenousy/regrag-ifc). RegRAG-IFC shows pipeline: IR -> SHACL -> verdict.
- bSDD ensures property naming consistency, prerequisite for IDS.

### 3.3 RAG over regulatory documents

Emerging pattern: chunk PDF building codes, embed (MiniLM, OpenAI embedding-001), store in Chroma/FAISS, retrieve top-k clauses, LLM compares visual description vs clause to flag compliance (Saudi codes assistant https://github.com/galall10/saudi-codes-assistant, TCVN copilot). Studies show LLMs hallucinate geometric reasoning, struggle with fall hazards [[51]](https://github.com/mudasir1214/mudasir-rag-llm-bim). RegTAI uses deterministic rule engine for bathroom area, bedroom window, kitchen ventilation, staircase width, then Gemini LLM for report generation [[52]](https://github.com/fares479/regtai-ai-decision-support-system) — this is the correct separation.

Recommended conversion: Regulation PDF -> Docling or PyMuPDFLoader -> RecursiveCharacterTextSplitter -> embeddings -> vector DB -> retrieval -> LLM drafts rule as Python function `def check(model: IfcFile) -> List[Violation]` using IfcOpenShell API -> human review -> compile to CP-SAT constraints or IDS file. LLM must NOT directly decide compliance; must generate code that is then executed deterministically and validated via IDS/bimtester.

## 4. Existing products and market gaps

| Product | Type | Inputs | Outputs | Pricing (2026) | Target user | DXF/IFC | Constraint capability | Limitations |
|---|---|---|---|---|---|---|---:|---|
| Maket.ai | Residential generative SaaS | Land size, building shape, room dims, adjacency, style prompt, sketch-to-AI | 2D floorplan, 3D, DXF, PDF, JPEG | Free 50 credits, Pro $30/mo, Premium $24/mo annual [[53]](https://www.maket.ai/pricing) [[54]](https://www.toolify.ai/tool/maket/?ref=embed) | Homeowners, small architects | DXF export confirmed, no IFC [[55]](https://www.maket.ai/blog/how-to-use-maket) | Adjacency, dimensions, multi-floor, versioning, agentic edit ("make kitchen 20% bigger") | No hard area guarantee, no building code, no IFC/BIM, residential only, vendor-claimed compliance |
| TestFit | Feasibility/generative site planner | Lot boundary, FAR, coverage, setbacks, parking, unit mix, pro forma | 3D massing, unit plans, Revit, AutoCAD, pro forma, thousands of options in seconds | Urban Planner $100/mo, Data Maps $250/mo, Site Solver from $8k/yr, Enterprise custom; older per-seat ~$3-5k/seat/yr [[56]](https://archigenai.com/testfit-feasibility-generative-design-review-2026.html) [[57]](https://illustrarch.com/articles/design-softwares/74579-testfit-review.html) | Developers, multifamily architects | Revit export, IFC via Revit, DXF via AutoCAD | Zoning, yield-on-cost, parking, but not room-level target area or adjacency graph; optimization is yield-driven | No detailed room adjacency control, no MEP, not for custom single-family, expensive |
| Finch3D | Graph-based parametric layout | Floor plate boundary, desired room counts, CO2, net internal area, custom graph rules | Floor plans with walls, full room loadout, performance graphs, Revit/Rhino/Grasshopper bidirectional streaming | Free plan manual editing, Plus from €49/mo [[58]](https://dataconomy.com/tools/finch-3d/) | Architects, parametric designers | Revit, Rhino, Grasshopper, IFC via Revit | Custom graph rules: adjacency, area via graph, patented graph tech [[59]](https://aecmag.com/cad/finch-untethered/) | Residential multifamily focus, rules require expertise, no automatic code checking, pricing opaque |
| Autodesk Forma (ex-Spacemaker) | Site planning + environmental | Site boundary, zoning, sun/wind/noise/embodied carbon, massing | 3D massing, environmental analyses (sun hours, daylight, solar, wind, noise, microclimate) [[60]](https://www.autodesk.com/in/products/forma/environmental-impact-analysis) | Standalone $185/mo, $1500/yr, AEC Collection $430/mo [[61]](https://illustrarch.com/articles/design-softwares/73363-autodesk-forma-review.html) | Urban planners, large AEC | Revit, Rhino, Dynamo, IFC, JSON, glTF | Zoning, environmental constraints, not room-level adjacency or target area | No interior layout generation, heavy Autodesk lock-in, acquired Spacemaker for $240M [[62]](https://adsknews.autodesk.com/en/pressrelease/autodesk-completes-acquisition-of-spacemaker-provider-of-ai-and-generative-design-enabled-urban-design-platform/) |
| Planner5D | Consumer interior/home design | Floor plan image recognition, AI design generator, 6000+ items catalog | 2D/3D, HD renders, no CAD/BIM | Free limited, Premium $9.99/mo or $59.99/yr [[63]](https://appadvice.com/app/planner-5d-ai-interior-design/606173978.amp) | Homeowners | No DXF/IFC professional | AI layout/furniture placement only | Not for construction docs |
| Hypar | Computational design platform | Functions in C#, Python, generates building systems via Elements library | IFC, JSON, glTF, Rhino, Revit, Excel, Dynamo, Grasshopper | Hosting private functions $10/mo for 5 funcs [[64]](https://architosh.com/2020/03/insider-hypar-looks-to-unlock-aec-industry-expertise-via-computational-design-ecosystem/), Elements Apache-2.0 open [[65]](https://github.com/magnetar-aec/elements) | Computational designers, firms | IFC support, Revit | Custom functions, space planning HyparSpace | Company appears less active 2024+, limited market traction, pricing now community-driven |
| Snaptrude | Cloud BIM + AI | RFP, brief, text prompt, site boundary, zoning | Full BIM LOD 300-350, Revit export, IFC, presentations | Free tier, paid from $30-57/mo, ~$500+/yr Pro [[66]](https://www.snaptrude.com/pricing) | Architects early-stage | IFC import/export, Revit, Rhino via ARES | AI agents: site analysis, zoning, envelope packing, program, floorplan, BIM | LOD not construction-ready, BIM quality depends on prompt, no hard code compliance |
| ARCHITEChTURES | Residential automated design | Building typology, parameters | IFC, DXF, XLSX, feasibility | Free limited, $39-50/mo [[67]](https://toolpilot.ai/collections/architecture-interior-design/products/architechtures) | Developers | IFC, DXF | Real-time geometry fitting | Limited to residential typologies, custom pricing for enterprise |

**Gap:** No product combines (a) hard target area enforcement ± tolerance, (b) adjacency graph hard enforcement, (c) building boundary exact, (d) circulation width and egress, (e) deterministic building-code checks via IDS, (f) editable DXF with layers/blocks/dims + vector PDF title blocks + IFC with property sets, (g) multi-floor vertical circulation, (h) open API. Maket lacks IFC and code; TestFit/Finch/Forma lack room-level hard constraints and DXF architectural semantics; Hypar is too generic; Snaptrude lacks determinism.

## 5. Recommended architecture and stack

**Design principles:**
- LLM for parsing and interaction only, never for final geometry or compliance.
- Neural for inspiration/diversity, solver for correctness.
- IFC as source of truth, DXF/PDF as derived views.
- All geometry operations auditable.

**Proposed pipeline:**

```
Spec (NL + JSON) 
  -> LLM Parser (Mistral/Llama-3) -> Structured IR (Building, Floors, Rooms {id, type, target_area, min/max_area, adjacency[], materials}, Boundary polygon, Code refs)
  -> Validation: JSON schema + IDS skeleton
  -> Proposal Generator (Hybrid):
        Branch A: Graph2Plan or HouseDiffusion fine-tuned on RPLAN + private dataset -> 10-20 coarse topologies (room boxes)
        Branch B: Procedural CP-SAT seed (OR-Tools) from IR alone
  -> Solver Refinement (OR-Tools CP-SAT + Topologic):
        Variables: x,y,w,h per room (int mm)
        Constraints: boundary inside, non-overlap, adjacency via shared edge >= door width, target area within tolerance, aspect ratio, circulation graph connectivity, corridor width >= code, egress path, daylight adjacency
        Objective: minimize area deviation + circulation + adjacency penalty + diversity term
  -> Geometry Builder: pythonocc-core + IfcOpenShell -> IfcWall, IfcDoor, IfcWindow, IfcSpace with Psets (areas, materials)
  -> Compliance Checker: ifctester IDS (area, adjacency, material) + custom Python checks (BIMTester Gherkin) + bSDD property validation
  -> Export: ezdxf (DXF R2010, layers: WALLS, DOORS, WINDOWS, DIMS, HATCH, TITLEBLOCK; blocks for doors/windows; dimensions; hatches) + ReportLab vector PDF with title block + IFC4 file
  -> Renderer: ezdxf drawing + matplotlib for preview PNG
  -> RAG Assist: LangChain + Chroma + bsdd for code explanation, not decision
```

**Technology stack verified:**

- Parsing: Llama-3 70B or Mistral via HuggingFace, Pydantic for IR, jsonschema.
- AI proposal: HouseDiffusion MIT fork or MaskPLAN MIT, PyTorch 2.x, CUDA. Fine-tune on RPLAN 60k vector. Keep weights downloadable.
- Solver: google-or-tools (Apache-2.0) CP-SAT, Topologicpy (AGPL-3.0, careful for SaaS -> use server-side not distribution, or replace with shapely for adjacency if AGPL issue).
- Geometry: IfcOpenShell 0.8.x (LGPL-3.0-or-later), pythonocc-core 7.8 (LGPL-3.0), CadQuery optional.
- DXF: ezdxf 1.4.x (MIT) – supports all required: walls as LWPOLYLINE, doors/windows as INSERT, layers, blocks, hatches via HATCH, dimensions via DIMENSION, title blocks via paperspace layout.
- PDF: ReportLab BSD-3-Clause for vector PDF, WeasyPrint BSD-3-Clause for reports.
- BIM compliance: ifctester (LGPL), bSDD client bsdd (MIT), BIMserver optional.
- Storage: PostgreSQL + PostGIS for boundaries, S3 for IFC/DXF.
- API: FastAPI, IFC Pipeline pattern (FastAPI + workers ifcclash, ifctester, ifcconvert).

**LLM boundaries:**
- Use: spec parsing NL -> IR, user chat ("make kitchen bigger"), RAG over codes to explain, not decide, generate IDS draft that human reviews.
- Must NOT: control final x,y,w,h coordinates, produce DXF directly, or output compliance verdict. Those must be solver/IDS deterministic.

**Comparison neural vs procedural vs hybrid:**

| Approach | Diversity | Hard constraint guarantee | Area accuracy | Code compliance | Data need | Compute | Verdict |
|---|---|---|---|---|---|---|---|
| Pure GAN/Diffusion | High | No | Low (±30%) | No | 60k+ | High GPU | Inspiration only |
| Pure CP-SAT procedural | Low | Yes 100% | High ±2% | Yes if coded | None | CPU | Sterile but compliant |
| Hybrid (recommended) | High (neural) + refined | Yes after solver | High ±3% | Yes via IDS | 60k for proposal | GPU+CPU | Best of both |

## 6. MVP roadmap

**Phase 0 (4 weeks) – Foundation:**
- Define IR JSON schema: building footprint polygon, floors array, rooms {type, target_area, min_area, max_area, adjacency[], material, needs_daylight}, circulation width, codes list.
- Set up ezdxf exporter: layers, blocks for door types, dims, title block with project data. Validate DXF opens in AutoCAD/ODA Viewer.
- Set up IfcOpenShell writer: IfcProject, IfcSite, IfcBuilding, IfcBuildingStorey, IfcWall, IfcDoor, IfcWindow, IfcSpace with Qto_SpaceBaseQuantities (NetFloorArea).
- Set up ReportLab vector PDF: A3 template, plan view, legend, title block.

**Phase 1 (8 weeks) – Solver-only generator:**
- Implement OR-Tools CP-SAT rectangular packing: input boundary (orthogonal), rooms with target areas, adjacency graph. Use non-overlap 2D, adjacency via distance <= threshold. Output 3 options varying staircase positions (like PlanForge).
- Add validation: area tolerance check, adjacency check, boundary check.
- Export to DXF/PDF/IFC.

**Phase 2 (8 weeks) – Neural proposal + solver hybrid:**
- Integrate HouseDiffusion (MIT) pretrained on RPLAN: input bubble diagram -> vector loops. Convert loops to initial boxes for CP-SAT warm start.
- Add MaskPLAN for partial input support.
- Fine-tune on 5k private residential plans with target areas.

**Phase 3 (6 weeks) – Compliance & BIM:**
- Author IDS files for area, material, property presence. Integrate ifctester validation: `python -m ifctester requirements.ids model.ifc -r Html`.
- Integrate ifcclash for door/wall clashes.
- bSDD client to validate materials.

**Phase 4 (6 weeks) – LLM + RAG + UX:**
- LLM parser: NL spec -> IR, with human-in-the-loop correction UI.
- RAG: ingest local building code PDFs (e.g., Kazakh SNiP), chunk, embed, retrieve, generate explanation + draft IDS rule as Python, human approve.
- Frontend: web canvas with ezdxf preview, editable drag (re-run solver), export buttons.

**Phase 5 (4 weeks) – Hardening:**
- Multi-floor: vertical alignment of shafts, stairs.
- Performance: caching, async workers.

Total MVP ~ 7-8 months with 3 engineers (1 AEC + 1 ML + 1 backend).

## 7. Validation strategy

- **Geometry:** Unit tests for CP-SAT: area within 3% of target, adjacency satisfied 100%, boundary inside 100%, non-overlap 100%, circulation connectivity via BFS. Property-based testing with random boundaries. Use Topologic to verify adjacency graph isomorphism.
- **Code compliance:** IDS validation via ifctester: `ids.validate(ifc)` must be 0 failures for mandatory specs. BIMTester Gherkin: `Scenario: All habitable rooms have window area >=10% floor area`. Each code rule has corresponding Python check function with provenance link to regulation PDF page.
- **DXF:** ezdxf audit `ezdxf audit file.dxf`, round-trip test: write -> read -> compare entity counts, layer names, block names. Open in AutoCAD, LibreCAD, ODA Drawings Explorer. Check vector PDF export via `ezdxf draw -o file.pdf`. Verify dimensions readable.
- **PDF:** ReportLab PDF/A compliance, vector check (no rasterized plans), title block fields present, scale bar, north arrow. Compare with PDF reference via PyMuPDF text extraction.
- **BIM:** IfcOpenShell validation `ifcopenshell.validate`, IDS checker HTML report, IFC viewer BIMserver or Bonsai visual inspection. Property sets present: Pset_WallCommon, Pset_DoorCommon, Qto_SpaceBaseQuantities. bSDD mapping check.

All validations automated in CI, with BCF export for failures.

## 8. Major DEAD ZONES

- **Hard area + adjacency + boundary simultaneously:** No open model guarantees all three; CP-SAT can but becomes NP-hard beyond ~15 rooms with non-rectangular boundaries. Scaling to 30+ rooms requires heuristics, may fail.
- **DXF semantic round-tripping:** DXF has no room concept; walls as lines lose BIM data. True round-trip DXF -> IFC -> DXF without loss is impossible; must keep IFC as master.
- **DWG write without ODA:** LibreDWG GPL-3.0 infects SaaS if distributed, and still misses ACAD 2024 features. Cannot legally offer native DWG export MIT-licensed; must rely on DXF or ODA converter proprietary.
- **Non-Manhattan and curved walls:** Most datasets (RPLAN) are Manhattan; HouseDiffusion claims non-Manhattan but trained data lacks it; CP-SAT rectangular model cannot handle curves without Topologic + OCC boolean which explodes complexity.
- **Multi-floor vertical circulation and MEP:** No dataset includes MEP, shafts, structural. Solver must enforce stair alignment, elevator core, which is 3D constraint, not researched.
- **Building codes as executable rules:** Codes are ambiguous, jurisdiction-specific, require human interpretation. Converting natural language code to SHACL/IDS automatically via LLM is unreliable; prior work RegRAG-IFC shows IR accuracy <70%. No open machine-readable code corpus for Kazakhstan.
- **Performance of pythonocc in headless containers:** OCCT heavy, Docker image ~2GB, geometry boolean slow for 100+ walls.
- **Topologic AGPL-3.0:** Using as cloud service may require open-sourcing if network use is considered distribution under AGPL; need legal review or replace with shapely + networkx (loses 3D).
- **RPLAN license:** Research-only, not commercial; commercial use requires negotiation. FloorPlanCAD CC-BY-NC-4.0 prohibits commercial use of annotations. Need private dataset or license.

## 9. Three strongest product opportunities

**1. Deterministic CP-SAT + IDS compliant generator for small residential (G+1) in emerging markets (Kazakhstan, India, Poland).**
- Opportunity: PlanForge example shows Indian builders need municipal bye-laws + Vastu + PDF/DXF/BOQ. No incumbent offers hard bye-law compliance. Use OR-Tools + ezdxf + ReportLab + ifctester with local SNiP rules encoded as IDS. Pricing $50-99/mo, target small firms, not enterprise. Gap: TestFit and Maket ignore local codes. Revenue via SaaS + BOQ.

**2. Hybrid proposal engine as headless API for Snaptrude/TestFit/Finch users who need editable DXF/PDF.**
- Opportunity: Existing tools lock output in their ecosystem (Revit only). Offer API: input bubble diagram + boundary -> return 5 IFC + DXF with layers/blocks + vector PDF + IDS report. Integrates via Hypar Elements (Apache-2.0) and IfcOpenShell. Sell to AEC tech platforms as OEM. Uses HouseDiffusion + CP-SAT refinement, which no competitor offers as API.

**3. RAG-assisted IDS authoring tool for BIM managers (compliance copilot).**
- Opportunity: BIM managers spend hundreds of hours manually checking TCVN/QCVN or similar. Tool: upload IFC + regulation PDFs, RAG retrieves clauses, LLM drafts IDS XML (verifier-aware as in Ishigaki-IDS arXiv:2606.08545 [[68]](https://arxiv.org/abs/2606.08545)), validates via ifctester, human approves. This is distinct from layout generation but leverages same stack (IfcOpenShell, ifctester, bSDD, LangChain). Can be upsold to layout configurator. Market: compliance checking is mandatory, willingness to pay higher than inspirational design.

## Sources

[1] ezdxf — Python interface to DXF, MIT, supports R12-R2018, drawing add-on (https://github.com/mozman/ezdxf)
[2] pythonocc-core — LGPL-3.0 wrapper (https://github.com/tpaviot/pythonocc-core)
[3] IfcOpenShell — LGPL-3.0-or-later, ifcclash, ifctester (https://github.com/IfcOpenShell/IfcOpenShell)
[4] ReportLab feedstock — BSD-3-Clause (https://github.com/anacondarecipes/reportlab-feedstock)
[5] WeasyPrint — BSD-3-Clause (https://github.com/Kozea/WeasyPrint)
[6] RPLAN dataset 80k+ (http://staff.ustc.edu.cn/~fuxm/projects/DeepLayout/index.html)
[7] House-GAN arXiv:2003.06988 (https://arxiv.org/pdf/2003.06988)
[8] House-GAN++ arXiv:2103.02574 (https://arxiv.org/abs/2103.02574)
[9] Graph2Plan arXiv:2004.13204 (https://arxiv.org/abs/2004.13204)
[10] HouseDiffusion arXiv:2211.13287 (https://arxiv.org/pdf/2211.13287v1)
[11] MaskPLAN CVPR 2024 GitHub (https://github.com/HangZhangZ/MaskPLAN)
[12] buildingSMART IDS GitHub (https://github.com/buildingSMART/IDS)
[13] BIMserver AGPL-3.0 (https://github.com/opensourceBIM/BIMserver)
[14] Maket.ai pricing $30/mo DXF export (https://www.maket.ai/pricing) and features (https://www.toolify.ai/tool/maket/?ref=embed)
[15] TestFit pricing ~$3k-5k/seat/yr (https://archigenai.com/testfit-feasibility-generative-design-review-2026.html) and tiered $100/$250/$8k (https://illustrarch.com/articles/design-softwares/74579-testfit-review.html)
[16] Finch3D pricing from €49/mo (https://dataconomy.com/tools/finch-3d/)
[17] Autodesk Forma $185/mo (https://illustrarch.com/articles/design-softwares/73363-autodesk-forma-review.html)
[18] Snaptrude pricing (https://www.snaptrude.com/pricing)
[19] ezdxf abstract and MIT (https://github.com/mozman/ezdxf) viewed
[20] ReportLab BSD (https://github.com/anacondarecipes/reportlab-feedstock)
[21] WeasyPrint BSD (https://github.com/Kozea/WeasyPrint)
[22] OCCT LGPL-2.1-only (https://github.com/OPEN-CASCADE-SAS/OCCT)
[23] pythonocc-core LGPL-3.0 (https://github.com/tpaviot/pythonocc-core)
[24] Topologic AGPL-3.0 (https://dev.opencascade.org/project/topologic)
[25] IfcOpenShell LGPL-3.0-or-later (https://github.com/IfcOpenShell/IfcOpenShell) – pip ifcclash etc
[26] IfcClash docs (https://docs.ifcopenshell.org/ifcclash.html)
[27] IfcTester docs (https://docs.ifcopenshell.org/ifctester.html)
[28] BIMserver AGPL-3.0 directory (https://github.com/opensourceBIM/BIMserver)
[30] House-GAN repo (https://github.com/ennauata/housegan)
[31] HouseGAN license discussion GPL-3.0 vendor (https://github.com/creative-graphic-design/design-generators/issues/17)
[32] HouseGAN++ repo (https://github.com/ennauata/houseganpp)
[33] HouseGAN++ demo (https://github.com/ennauata/houseganpp)
[34] Graph2Plan repo (https://github.com/zzilch/graph2plan)
[35] Graph2Plan data processing (https://github.com/zzilch/graph2plan) – boundary+graph
[36] HouseDiffusion repo (https://github.com/aminshabani/house_diffusion)
[37] HouseDiffusion project page (https://aminshabani.github.io/housediffusion) via arXiv page
[38] HouseDiffusion dataset note MagicPlan alternative (https://github.com/aminshabani/house_diffusion)
[39] Boundary-Constrained Diffusion Models arXiv:2602.01949 (https://arxiv.org/pdf/2602.01949v1)
[40] MaskPLAN CVPR poster (https://cvpr.thecvf.com/virtual/2024/poster/31566)
[41] Tell2Design arXiv:2311.15941 (https://arxiv.org/pdf/2311.15941v1)
[42] CubiCasa5K GitHub MIT (https://github.com/schulzdaniel/cubicasa5k) and dataset Zenodo
[43] FloorPlanCAD CC-BY-NC-4.0 (https://floorplancad.github.io/)
[44] poolpet/floorplan4 OR-Tools CP-SAT Polish WT 2002 (https://github.com/poolpet/floorplan4)
[45] IDS standard (https://github.com/buildingSMART/IDS)
[46] IDS.py toolkit GSoC (http://blog.ifcopenshell.org/2021/08/idspy-toolkit-as-result-of-google.html)
[47] ifcbimtester Gherkin (https://github.com/IfcOpenShell/IfcOpenShell) table
[48] Solibri Checking API (https://solibri.github.io/Developer-Platform/latest/getting-started.html)
[49] ACC API (https://forge.autodesk.com/blog/new-api-type-autodesk-construction-cloud)
[50] RegRAG-IFC UK Approved Doc M (https://github.com/ysenousy/regrag-ifc)
[51] mudasir-rag-llm-bim fall hazards (https://github.com/mudasir1214/mudasir-rag-llm-bim)
[52] RegTAI deterministic + Gemini (https://github.com/fares479/regtai-ai-decision-support-system)
[53] Maket DXF/PDF export how-to (https://www.maket.ai/blog/how-to-use-maket)
[54] Maket generative design (https://www.maket.ai/?via=aiaaascatalog)
[55] Maket draw from scratch DXF (https://www.maket.ai/blog/draw-a-floor-plan-from-scratch)
[56] TestFit review (https://archigenai.com/testfit-feasibility-generative-design-review-2026.html)
[57] TestFit pricing tiers (https://illustrarch.com/articles/design-softwares/74579-testfit-review.html)
[58] Finch3D freemium €49 (https://dataconomy.com/tools/finch-3d/)
[59] Finch graph tech (https://aecmag.com/cad/finch-untethered/)
[60] Autodesk Forma environmental analyses (https://www.autodesk.com/in/products/forma/environmental-impact-analysis)
[61] Forma pricing $185/mo (https://illustrarch.com/articles/design-softwares/73363-autodesk-forma-review.html)
[62] Spacemaker acquisition $240M (https://adsknews.autodesk.com/en/pressrelease/autodesk-completes-acquisition-of-spacemaker-provider-of-ai-and-generative-design-enabled-urban-design-platform/)
[63] Planner5D premium $9.99/mo (https://appadvice.com/app/planner-5d-ai-interior-design/606173978.amp)
[64] Hypar hosting $10/mo 5 funcs (https://architosh.com/2020/03/insider-hypar-looks-to-unlock-aec-industry-expertise-via-computational-design-ecosystem/)
[65] Hypar Elements Apache-2.0 forever open (https://github.com/magnetar-aec/elements)
[66] Snaptrude pricing page (https://www.snaptrude.com/pricing)
[67] ARCHITEChTURES pricing (https://toolpilot.ai/collections/architecture-interior-design/products/architechtures)
[68] Ishigaki-IDS open-weight verifier-aware LLM for IDS (https://arxiv.org/abs/2606.08545)

*Note on verification: Stars, forks, last commit for many repos marked unverified where GitHub API not queried; licenses verified via README, feedstock, or LICENSE file where available, otherwise marked unverified per requirement. All links opened and verified in this session.*
