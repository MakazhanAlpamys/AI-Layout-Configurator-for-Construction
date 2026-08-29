# AI Layout Configurator for Construction — Comprehensive Technical Research


## 1. Open-Source Technologies

### 1.1 DXF/DWG Generation & Manipulation

**ezdxf** — Python package for DXF creation and manipulation.
- **Official Link**: https://ezdxf.mozman.at/
- **License**: MIT
- **Stars/Forks**: Not found (documentation site; repository on GitHub)
- **Last Commit**: v1.4.4 released May 14, 2026
- **Tech Stack**: Python 3.10+, optional C-extensions, NumPy, pyparsing, fontTools
- **DXF Support**: Read/write R12, R2000, R2004, R2007, R2010, R2013, R2018; read-only R13/R14 and pre-R12
- **Key Features**: Walls, doors, windows via LINE/LWPOLYLINE/BLOCK entities; layers, blocks, hatches, dimensions supported; MTEXT, text-to-path add-on; CTB/STB plot styles; ODA File Converter integration for DWG
- **Add-ons**: dxf2code (parametric code generation), r12writer (fast write), iterdxf (>5GB files), importer, pycsg (CSG), openscad interface, odafc (ODA converter)
- **Maintenance**: Active; documentation updated 2026

**LibreDWG** — GNU C library for DWG read/write.
- **Official Link**: https://www.gnu.org/software/libredwg/
- **License**: GPL
- **Version**: 0.14.1 (July 2026), 0.14 (June 2026), 0.13.4 (March 2026)
- **Coverage**: ~99% read coverage claimed
- **Status**: Beta; decoder reads all DWG versions, some advanced R2010+ objects fail and are skipped; DXB support added
- **Limitations**: Write support for newer versions still maturing; GPL license may restrict commercial use

**Note**: ODA File Converter (proprietary, freeware) can be used alongside ezdxf for robust DWG round-tripping.

### 1.2 CAD & Geometry Kernels

**FreeCAD** — Open-source parametric 3D CAD modeler.
- **Official Link**: https://www.freecad.org/
- **License**: LGPLv2.1 with exception
- **Latest Release**: 1.1 (March 2026); weekly builds against OCCT 8.0.0p1
- **Geometry Kernel**: Open CASCADE Technology (OCCT)
- **Features**: BREP, NURBS, boolean operations, fillets, STEP/IGES support, full parametric model, Python scripting
- **Maintenance**: Very active

**Open CASCADE Technology (OCCT)** — Industrial-grade C++ geometry kernel.
- **Official Link**: https://www.opencascade.com/
- **License**: LGPLv2.1 with exception
- **Latest Version**: 8.0.0 (May 2026); over 500 changes since 7.9.0
- **Used By**: FreeCAD, KiCad, many commercial CAD systems
- **Key Features**: B-Rep modeling, boolean operations, fillets, STEP/IGES I/O, meshing
- **Limitations**: Complex C++ codebase; IGES/STEP parsers have had CVEs

**CadQuery** — Python parametric CAD scripting on OCCT.
- **Official Link**: https://github.com/CadQuery/cadquery
- **License**: Apache 2.0
- **Tech Stack**: Python, OCCT backend
- **Key Features**: Script-based parametric modeling, stateless free-function API (2.5 release)
- **Maintenance**: Active (June 2026 commit)

### 1.3 BIM/IFC Tools

**IfcOpenShell** — Open-source IFC library and geometry engine.
- **Official Link**: https://github.com/IfcOpenShell/IfcOpenShell
- **License**: LGPL
- **Last Commit**: July 2026; PyPI release April 2026
- **IFC Support**: IFC2x3 TC1, IFC4 Add2 TC1, IFC4x1, IFC4x2, IFC4x3 Add2
- **Tech Stack**: C++ core, Python bindings, Blender integration (Bonsai)
- **Key Features**: Geometry operations, property management, format conversion, shape builder
- **Maintenance**: Very active

**Bonsai (formerly BlenderBIM)** — Blender add-on for IFC-based BIM.
- **Official Link**: https://extensions.blender.org/add-ons/bonsai/
- **License**: Open source (within Blender ecosystem)
- **Requires**: Blender 4.4+, Bonsai 0.8.2+
- **Key Features**: Native IFC editing in Blender, BIM authoring environment
- **Integration**: IFC-Bonsai-MCP connects LLMs to IFC workflows with 50+ MCP tools

**Note**: IfcOpenShell and Bonsai form the most mature open-source BIM toolchain available.

### 1.4 Vector PDF Generation

**ReportLab** — Industry-strength PDF generation.
- **Official Link**: https://www.reportlab.com/
- **License**: BSD
- **Requires**: Python 3.7+
- **Key Features**: Vector PDF generation, charts, SVG conversion via svglib
- **Maintenance**: Active (mirror updated August 2026)

**WeasyPrint** — HTML/CSS to PDF rendering engine.
- **Official Link**: https://weasyprint.org/
- **License**: BSD
- **Requires**: Python 3.10+
- **Latest Commit**: August 18, 2026
- **Key Features**: Modern CSS (flexbox, grid, paged media), PDF/A support
- **Maintenance**: Very active

**Recommendation**: ReportLab for programmatic CAD-to-PDF rendering; WeasyPrint for report-style documents with rich formatting.

### 1.5 Summary Table: Open-Source Technologies

| Project | License | Purpose | DXF/DWG | IFC | PDF | Status |
|---------|---------|---------|---------|-----|-----|--------|
| ezdxf | MIT | DXF R/W | Full R12-R2018 | No | Via add-on | Active |
| LibreDWG | GPL | DWG R/W | Partial (via DXB) | No | No | Beta |
| FreeCAD | LGPL | CAD modeling | Via add-ons | Via BIM WB | Via add-ons | Active |
| OCCT | LGPL | Geometry kernel | No | No | No | Active |
| CadQuery | Apache 2.0 | Parametric CAD | Export | No | No | Active |
| IfcOpenShell | LGPL | IFC library | No | Full IFC | No | Active |
| Bonsai | Open | BIM authoring | No | Full IFC | No | Active |
| ReportLab | BSD | PDF generation | No | No | Vector PDF | Active |
| WeasyPrint | BSD | PDF generation | No | No | Vector PDF | Active |


## 2. AI and Algorithmic Floor-Plan Generation

### 2.1 Neural Network Approaches

**HouseGAN / HouseGAN++**
- **Papers**: House-GAN (ECCV 2020); HouseGAN++ follow-up
- **Approach**: Relational GANs for graph-constrained layout generation
- **Dataset**: Trained on LIFULL HOME’s database (117,587 floor plans from 5M total)
- **Code**: https://github.com/ennauata/housegan — last updated ~7 months ago
- **Limitations**: Constraint handling is graph-based; hard constraints (exact room areas) not guaranteed

**Graph2Plan** (SIGGRAPH 2020)
- **Official**: https://github.com/zzilch/Graph2plan (last commit 2023)
- **Approach**: Combines deep neural networks with user-in-the-loop design; constraints as layout graphs
- **Input**: Layout graph (room adjacencies, boundaries)
- **Output**: Floor plan polygons
- **Dataset**: RPLAN
- **Limitations**: No recent updates; post-processing uses MATLAB

**Raster2Seq** (SIGGRAPH 2026)
- **Approach**: Polygon sequence generation for floorplan reconstruction
- **Performance (vendor-claimed)**: CubiCasa5K RoomF1 = 88.7
- **Code**: https://huggingface.co/haopt/Raster2Seq (PyTorch checkpoints available)

**HouseTune** (AAAI 2026)
- **Approach**: Two-stage floorplan generation with LLM assistance + diffusion models
- **Code**: https://github.com/NatalieZZY/HouseTune

**GRE-Diff**
- **Approach**: Diffusion-based framework with Gaussian room embeddings for constraint-guided layout generation
- **Key Feature**: GuidanceNet predicts room embeddings; DenoisingNet generates polygonal layouts

**LLM + RLVR for Floor Plans** (arXiv 2026)
- **Approach**: Fine-tune LLM on real plans + reinforcement learning with verifiable rewards
- **Key Feature**: RLVR improves adherence to topological and numerical constraints, discourages invalid/overlapping outputs
- **Status**: Research, no production-ready implementation found

### 2.2 Procedural & Solver-Based Approaches

**DPLAN** (2026)
- **Approach**: Graph-based prototype generating floor plans from door and non-adjacency constraints
- **Implementation**: Python with interactive constraint specification

**Graph-Rules Method** (2026)
- **Approach**: Multi-constraint problem solved with graph algorithms; rectangular boundaries with adjacency/non-adjacency constraints

**RoomRubiks** (2026)
- **Approach**: Constraint-based framework within Rhino-Grasshopper; transforms connectivity graphs via breadth-first search

### 2.3 Datasets

| Dataset | Size | Format | License | Notes |
|---------|------|--------|---------|-------|
| RPLAN | ~81K floor plans | Raster + vector | Research | Most widely used benchmark |
| LIFULL | ~124K floor plans | Vector | Research | 5M total available |
| CubiCasa5K | 5,000 samples | SVG | Research | 80+ categories; Finnish residential |
| FloorPlanCAD | 15,000+ CAD drawings | SVG | Research | Project shut down 2022 |

### 2.4 Constraint Support Assessment

**Critical Finding**: Most academic models (HouseGAN, Graph2Plan, diffusion models) generate *plausible* layouts but **do not guarantee** hard constraints like:
- Exact room areas
- Precise adjacency enforcement
- Building code compliance
- DXF/IFC round-tripping

**Exception**: Solver-based approaches (constraint satisfaction, graph algorithms) can enforce hard constraints but lack the generative variety of neural methods.

**Hybrid approaches** (LLM + RLVR, GRE-Diff) show promise for constraint-aware generation.


## 3. Building-Code Compliance, BIM, and RAG

### 3.1 Open-Source Rule Engines

**aeclib** — Open-source computational logic for building codes
- **Official**: https://github.com/aeclib/aeclib
- **License**: Not found (open-source)
- **Last Commit**: June 2026
- **Approach**: Translates building code provisions into executable, testable Python functions
- **Dependencies**: None
- **Status**: Active

**Normatia** — Spanish building code compliance platform
- **Official**: https://github.com/normatia/normatia
- **Last Commit**: April 2026
- **Key Feature**: MCP server for AI access to Spanish building code data

**CBECC** — Building energy code compliance
- **Official**: https://github.com/CBECC-software/cbecc
- **Last Commit**: May 2026
- **Approach**: EnergyPlus/OpenStudio-based energy code compliance

### 3.2 RAG for Regulatory Documents

**Building Code RAG Assistant**
- **Official**: https://github.com/WUYAC2333/building-code-rag-assistant
- **Last Commit**: April 2026
- **Key Features**: Clause-level retrieval, traceable citations, similarity thresholding

**Regulatory-RAG** — Production RAG for Russian regulations
- **Performance (vendor-claimed)**: 7.7/10 correctness, 0.936 faithfulness, 100% out-of-scope abstain
- **Cost**: $0.01/query

**AEC-RAG Dataset**
- **Official**: https://huggingface.co/lumen-models/aec-rag-dataset
- **Content**: Technical dialogues between BIM Auditor and GPT Expert on regulatory compliance

### 3.3 IFC Interoperability & MCP Integration

**MCP4IFC** — LLM + IFC integration framework
- **Approach**: Standardizes tool discovery and invocation via Model Context Protocol

**IFC-Bonsai-MCP** — LLM to Blender/Bonsai IFC workflows
- **Key Features**: 50+ MCP tools; RAG-powered knowledge retrieval; Python/Trimesh geometry generation
- **RAG**: Local vector index with sentence-transformers/all-MiniLM-L6-v2

### 3.4 Recommended Compliance Strategy

1. **Rule Encoding**: Translate building codes into deterministic Python functions using aeclib pattern
2. **Validation**: Run geometry against rule engine after generation
3. **RAG for Reference**: Use RAG for code interpretation during specification parsing, NOT for final compliance decisions
4. **IFC Export**: Use IfcOpenShell for BIM-compliant output with property sets for compliance metadata


## 4. Existing Products and Market Gaps

### 4.1 Proprietary Products

**Maket.ai**
- **Input**: Text prompts, sketches, PDF/JPG/PNG upload
- **Output**: Dimensioned layouts with walls, doors, windows, furniture; DXF export
- **Target**: Homeowners, residential design
- **Key Feature**: Chat-based editing, up to 4 stories
- **Limitations**: Residential focus; limited commercial/industrial; constraint capabilities unverified

**TestFit**
- **Input**: Site parameters, building program
- **Output**: Site plans, building layouts, cost estimates
- **Target**: Real estate feasibility, developers
- **Pricing**: $195/mo (Parking Solver)
- **Key Feature**: Deterministic engine, real-time generation
- **Limitations**: Concept-stage only; no detailed BIM/IFC output; DXF support not clearly documented

**Finch3D**
- **Input**: Building massing (Revit/Rhino), constraints (unit mix, daylight, adjacency)
- **Output**: Floor plan variants
- **Target**: AEC firms, schematic design
- **Key Feature**: Constraint-based AI, Revit integration
- **Limitations**: Proprietary; pricing unverified for 2026; BIM integration limited

**Autodesk Forma**
- **Input**: Massing models, site data
- **Output**: Floor plan options, environmental analysis
- **Target**: Urban planning, early-stage design
- **Key Feature**: Building Layout Explorer (generative AI)
- **Limitations**: Proprietary; cloud-based; no open API for automation; DXF/IFC export via ecosystem

**Planner5D**
- **Input**: Sketches, images, PDFs, manual plans
- **Output**: 2D/3D floor plans, renderings
- **Target**: Homeowners, remodelers, interior design
- **Pricing**: Free/Basic, $4.99/mo Premium, $14.99/mo Professional
- **Key Feature**: AI floor plan recognition, Smart Wizard
- **Limitations**: Not a CAD/BIM tool; limited constraint handling

**Hypar**
- **Input**: Building requirements, firm-specific logic
- **Output**: Plans, 3D models
- **Target**: AEC firms, small studios
- **Pricing**: Free (community) / $250/mo (Pro); $100/user/mo or $1,000/user/yr
- **Key Feature**: Cloud-based generative design, automates firm-specific logic
- **Limitations**: Proprietary; requires technical familiarity

### 4.2 Market Gaps

| Gap | Description |
|-----|-------------|
| **Open-source, constraint-aware generation** | No open-source tool combines hard constraint satisfaction (room areas, adjacencies, code compliance) with generative variety |
| **Fully editable DXF round-tripping** | Most tools export DXF but cannot re-import and edit generated layouts |
| **BIM-native generative design** | Few tools generate IFC directly; most export to proprietary formats first |
| **Code compliance as generation constraint** | No product integrates building code validation *during* generation; all validate after |
| **Transparent AI reasoning** | Black-box generation without explainability for compliance-sensitive decisions |


## 5. Recommended Architecture and Stack

### 5.1 Architectural Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        USER INTERFACE LAYER                                 │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────────┐  │
│  │ Spec Parser │  │ Interactive │  │ Option      │  │ DXF/PDF/IFC     │  │
│  │ (LLM)       │  │ Editor      │  │ Selector    │  │ Export          │  │
│  └─────────────┘  └─────────────┘  └─────────────┘  └─────────────────┘  │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
┌─────────────────────────────────────────────────────────────────────────────┐
│                        ORCHESTRATION LAYER                                  │
│  ┌─────────────────────────────────────────────────────────────────────┐  │
│  │                    Constraint Solver (Python)                        │  │
│  │  • Room areas • Adjacencies • Building boundaries • Code rules      │  │
│  └─────────────────────────────────────────────────────────────────────┘  │
│                                      │                                      │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────────────┐  │
│  │ Procedural Gen  │  │  Hybrid AI      │  │   Validation Engine     │  │
│  │ (Graph/Solver)  │  │  (LLM + Solver) │  │   (aeclib-based)        │  │
│  └─────────────────┘  └─────────────────┘  └─────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
┌─────────────────────────────────────────────────────────────────────────────┐
│                        OUTPUT GENERATION LAYER                             │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────────┐  │
│  │ DXF Export  │  │ PDF Export  │  │ IFC Export  │  │ BIM Validation  │  │
│  │ (ezdxf)     │  │ (ReportLab) │  │ (IfcOpenShell)│  │ (IfcOpenShell) │  │
│  └─────────────┘  └─────────────┘  └─────────────┘  └─────────────────┘  │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 5.2 Technology Stack

| Layer | Technology | Justification |
|-------|------------|---------------|
| **Backend** | Python 3.10+ | Ecosystem for CAD, ML, and geometry |
| **Geometry Kernel** | OCCT (via CadQuery or PythonOCC) | Production-grade, LGPL, powers FreeCAD |
| **DXF Generation** | ezdxf (MIT) | Most mature open-source DXF library |
| **IFC Export** | IfcOpenShell (LGPL) | Industry-standard open IFC library |
| **PDF Generation** | ReportLab (BSD) | Vector PDF, proven in production |
| **Constraint Solving** | Python + OR-Tools / python-constraint | Hard constraint satisfaction |
| **Procedural Generation** | Graph algorithms + slicing tree | Deterministic, constraint-guaranteed |
| **AI Generation** | Fine-tuned diffusion + constraint guidance | Generative variety with constraint awareness |
| **LLM** | Open-source (Llama 3.x) or API | Spec parsing, user interaction ONLY |
| **Code Compliance** | aeclib + custom rules | Deterministic rule evaluation |
| **RAG** | LangChain + Chroma/FAISS | Regulatory document retrieval |
| **API** | FastAPI | Modern, async, OpenAPI |

### 5.3 Where LLMs Should (and Should NOT) Be Used

**✅ LLM-appropriate:**
- Parsing natural language technical specifications
- Conversational user interaction
- Generating constraint descriptions from user intent
- RAG-based regulatory reference retrieval (for user information, NOT final decisions)

**❌ LLM NOT appropriate (must use deterministic methods):**
- Final geometry generation (use solvers/procedural methods)
- Code compliance decisions (use aeclib/rule engine)
- DXF/PDF geometry placement (use ezdxf/ReportLab)
- Room area and adjacency calculations (use constraint solver)

### 5.4 Neural vs Procedural vs Hybrid Recommendation

| Approach | Pros | Cons | Recommendation |
|----------|------|------|----------------|
| **Pure Neural** | Variety, novelty | No hard constraint guarantee | ❌ Not for production |
| **Pure Procedural** | Constraint guarantee | Limited variety | ✅ For baseline generation |
| **Constraint Solver** | Hard constraints guaranteed | Computationally expensive | ✅ For compliance validation |
| **Hybrid (LLM + Solver)** | Natural input + constraint guarantee | Complexity | ✅ **Recommended** |

**Recommended Hybrid Architecture**:
1. LLM parses spec → extracts constraints
2. Constraint solver validates feasibility
3. Procedural generator creates candidate layouts (guaranteeing constraints)
4. Diffusion model fine-tunes layout aesthetics (with constraint feedback)
5. Rule engine validates code compliance
6. Failed candidates → loop back with adjusted parameters


## 6. MVP Roadmap

### Phase 1: Foundation (Weeks 1-4)
- Set up Python backend with FastAPI
- Implement constraint solver for room areas, adjacencies, boundaries
- Basic procedural layout generator (graph-based)
- DXF export via ezdxf
- PDF export via ReportLab

### Phase 2: AI Integration (Weeks 5-8)
- Fine-tune open-source LLM (Llama 3.x) for spec parsing
- Implement RAG pipeline for building codes
- Integrate diffusion-based layout refinement
- Build validation engine with aeclib

### Phase 3: BIM & Compliance (Weeks 9-12)
- IFC export via IfcOpenShell
- Code compliance rule encoding (start with one jurisdiction)
- BIM validation pipeline
- Round-trip DXF import (edit → re-export)

### Phase 4: UI & Polish (Weeks 13-16)
- Web UI for spec input and option browsing
- Interactive layout editor
- Export dashboard (DXF/PDF/IFC)
- Performance optimization


## 7. Validation Strategy

### Geometry Validation
- **Self-intersection**: Shapely `is_valid` check
- **Room areas**: ± tolerance from spec
- **Adjacencies**: Graph connectivity verification
- **Building boundary**: Polygon containment check
- **Wall thickness**: Minimum/maximum enforcement

### Code Compliance Validation
- Encode rules as deterministic Python functions (aeclib pattern)
- Run after generation, before output
- Generate compliance report with violations
- Loop correction for fixable violations

### DXF Validation
- Round-trip test: export → import → compare geometry
- Version compatibility: test R2010, R2013, R2018
- Entity support: walls (LWPOLYLINE), doors/windows (BLOCK), dimensions, hatches

### PDF Validation
- Vector vs raster check (all text/lines must be vector)
- Layer preservation
- Scale accuracy
- Title block completeness

### BIM/IFC Validation
- IfcOpenShell `ifcopenshell.validate`
- IFC schema compliance (IFC4x3)
- Property set completeness
- Geometry validity


## 8. Major DEAD ZONES

| Dead Zone | Description | Mitigation |
|-----------|-------------|------------|
| **DWG Write Support** | LibreDWG write support for newer versions is incomplete | Use ODA File Converter (freeware) via ezdxf odafc add-on |
| **Hard Constraint Guarantees in Neural Models** | No open-source neural model guarantees room areas, adjacencies | Use hybrid: procedural/solver for constraints, neural for aesthetics only |
| **Open-Source Floor Plan Datasets** | Most datasets are research-only; commercial use unclear | Build proprietary dataset or license appropriately |
| **Building Code Rule Encoding** | No comprehensive open-source rule library for all jurisdictions | Start with one jurisdiction; build incrementally |
| **Round-Trip DXF Editing** | Limited open-source support for editing complex DXF | Build custom editor or integrate with existing open-source CAD |
| **Production-Ready AI Layout Models** | Academic models lack production hardening | Build custom hybrid system; don't rely solely on research code |
| **IFC Generation from Generated Layouts** | IfcOpenShell requires careful schema mapping | Build dedicated IFC exporter with property set mapping |
| **Performance at Scale** | No benchmarks for generating 100+ options/min | Design for async processing; use C-optimized libraries |


## 9. Three Strongest Product Opportunities

### Opportunity 1: Open-Source Constraint-Aware Layout Generator
**Differentiation**: First open-source tool that *guarantees* hard constraints (areas, adjacencies, code compliance) while generating multiple layout options.
**Target**: AEC firms needing transparent, auditable design automation.
**Technical Edge**: Hybrid solver + procedural generation; not black-box AI.

### Opportunity 2: BIM-Native Generative Design with IFC Round-Tripping
**Differentiation**: Generate IFC directly (not just DXF) with full property sets, enabling seamless Revit/Archicad import.
**Target**: BIM managers, large AEC firms.
**Technical Edge**: IfcOpenShell + Bonsai integration; IFC4x3 compliance.

### Opportunity 3: Code-Compliance-as-a-Service for Generative Design
**Differentiation**: Real-time building code validation during layout generation, with explainable violation reports.
**Target**: Architects, developers, code officials.
**Technical Edge**: aeclib-based rule engine + RAG for regulatory reference; deterministic, auditable.


## 10. Key Recommendations

1. **Build hybrid, not pure AI**: Use neural methods for variety and aesthetics; use solvers for hard constraints. Never let LLMs control final geometry.

2. **Prioritize DXF over DWG**: ezdxf (MIT) is production-ready; LibreDWG (GPL) has limitations. Use ODA File Converter for DWG when needed.

3. **Invest in IFC early**: IfcOpenShell is mature; BIM integration is a major differentiator from competitors.

4. **Start with one building code**: Encode one jurisdiction's rules completely before expanding. Use aeclib pattern.

5. **Build the validation engine first**: Before any AI, build deterministic validation (geometry + code). This becomes your quality gate.

6. **Use RAG for reference, not decisions**: RAG can help users understand codes but must not determine compliance.

7. **Open-source the core**: Differentiate by making the constraint solver open-source; monetize through enterprise features (UI, integrations, support).