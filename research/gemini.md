Architecture of an AI Layout Configurator for Construction: from Technical Specifications to DXF, IFC and Vector PDF Drawings
Analysis of Open Technologies: CAD/BIM Kernels, DXF/DWG/PDF Generation and Headless Rendering
Designing the architecture of a software system for automated generation of layout solutions requires a strict separation of functional responsibilities between the mathematical kernel for geometric computation, specialized 2D drawing serialization libraries, the BIM object model and the subsystem for generating construction documentation. Developing a scalable serverless service is based on evaluating existing open libraries by their ability to handle complex construction primitives, preserve data integrity in reverse transformations (round-tripping) and operate in an autonomous service without a graphical interface (headless mode).
Functional Capabilities of Open Libraries and Platforms
The analysis of key open tools shows substantial differences in support for basic construction primitives and graphical structures:
Tool
Walls and partitions
Doors and windows
Layer structure
Blocks and embedded entities
Hatches
Dimension lines
Title Blocks
Vector PDF export
DXF Round-tripping
ezdxf
Lines / Polylines
Blocks (INSERT)
Supported (LAYERS)
Supported (BLOCKS)
Supported (HATCH)
Supported (DIMENSION)
Supported in Paperspace
Supported (ezdxf.draw)
Yes (Preserves third-party XData)
LibreDWG
Low-level C structures
Low-level C structures
DWG layer tables
DWG blocks
Partial
Partial
As part of the drawing
No (Requires converters)
Partial (DWG ↔ DXF)
FreeCAD
Arch/Draft parametrics
Arch components
Full support
Supported
Supported
Supported
Auto-generation in TechDraw
Yes (via TechDraw / Qt)
Yes (via ezdxf/ODA)
Open CASCADE
B-Rep topology
B-Rep topology
Conceptually in XDE
Supported in XDE
Not applicable (3D kernel)
3D annotations
No
No (3D only)
Via external converters
CadQuery
Solid Extrusions / Workplanes
Assemblies
Limited
Nested assemblies
No
2D sketches
No
Export to SVG/DXF
Output DXF
IfcOpenShell
IfcWallStandardCase
IfcDoor, IfcWindow
IfcPresentationLayer
IfcTypeProduct (Styles)
IfcFillAreaStyle
IfcDimensionCurve
IfcDocumentInformation
No (Pure IFC)
IFC Import/Export
Bonsai (BlenderBIM)
Native IFC Walls
Native IFC Fillings (openings)
IFC layers and classes
IFC components
Section fill
Blender/IFC annotations
Sheet layout
Yes (via SVG/PDF render)
Output DXF/IFC
ReportLab
2D vector paths
2D vector paths
No (Canvas context)
Templates (Flowables)
Tiled fill
Manual lines/text
Full support (Canvas)
Direct PDF generator
No
WeasyPrint
CSS Border elements
CSS blocks
No
HTML templates
CSS Patterns
CSS absolute positioning
HTML/CSS Paged Media
Yes (HTML/CSS to PDF)
No

Systematic Review of Open Projects
ezdxf
The ezdxf library is a specialized Python package for reading, creating and modifying DXF files of independent versions (from R12 to R2018)1.
Official link: https://github.com/mozman/ezdxf
[cite: 1]
Purpose: Manipulation of DXF data structures without dependence on proprietary CAD systems, preserving third-party extended data (XData)1.
Technology stack: Python, Cython (C extensions to speed up parsing)1.
License / SPDX: MIT1.
Repository metrics: 3.3k stars, 400 forks, active maintenance1.
Distribution availability: Source code on GitHub, packages on PyPI (pip install ezdxf) and Conda-Forge1.
Performance source: Claimed by the developer (use of Cython provides high processing speed for files larger than 100 MB)1.
Maintenance status: Active project, regular updates1.
Limitations: Not a computational geometry kernel. The library does not autonomously compute wall intersections or clean up corners. The PDF export module (ezdxf.addons.drawing) relies on heavy third-party dependencies (Matplotlib or PySide6), which makes it harder to use in ultra-lightweight containers1.
LibreDWG
The GNU LibreDWG library provides low-level decoding and encoding of the binary DWG format5.
Official link: https://github.com/libredwg/libredwg
[cite: 7]
Purpose: Reading and writing DWG files without using Autodesk or ODA libraries5.
Technology stack: C (ANSI C), C++5.
License / SPDX: GPL-3.0-or-later5.
Repository metrics: 1.2k stars, 250 forks5.
Distribution availability: C source code, Conda-Forge builds, Linux packages5.
Performance source: Independent measurements by the production community.
Maintenance status: Active project within the GNU free software initiative5.
Limitations: The strict viral copyleft restrictions of GPLv3 rule out direct dynamic or static linking with the proprietary code of a cloud SaaS platform5. Writing DWG back for complex parametric BIM objects remains unstable.
FreeCAD
FreeCAD is a parametric 3D modelling system with mature Arch (BIM) and Draft (2D drafting) modules9.
Official link: https://github.com/FreeCAD/FreeCAD
[cite: 10]
Purpose: Full-featured CAD/BIM design, parametric analysis and documentation9.
Technology stack: C++, Python, Qt, Open CASCADE Technology10.
License / SPDX: LGPL-2.1-or-later10.
Repository metrics: 33k stars, 5.9k forks10.
Distribution availability: Binary builds for all OSes, headless Python module FreeCAD.so9.
Performance source: Independently measured performance of the OCCT kernel.
Maintenance status: Active project with many contributors10.
Limitations: High overhead for initializing the graphical and architectural subsystems when invoked in headless mode; increased RAM consumption when processing hundreds of layouts in parallel.
Open CASCADE Technology (OCCT)
An industrial boundary representation (B-Rep) kernel used to compute complex 3D operations and intersections of geometric solids.
Official link: https://dev.opencascade.org / https://github.com/Open-Cascade-SAS
[cite: 13]
Purpose: Computational geometry foundation for building engineering and construction software.
Technology stack: C++11/C++14.
License / SPDX: LGPL-2.1-only with a library exception (LGPL Exception).
Repository metrics: Corporate repository managed by Open Cascade SAS.
Distribution availability: C++ SDK, Python wrappers (Python-OCC, OCP).
Performance source: Industrial benchmarks of mechanical and architectural CAD.
Maintenance status: Active commercial open-source.
Limitations: Low-level C++ API. No built-in domain concepts of building structures (walls, floor build-ups, openings), which requires writing a custom layer of architectural primitives.
CadQuery
CadQuery is a parametric script-based modelling system built on C++ wrappers of the Open CASCADE kernel (OCP)14.
Official link: https://github.com/cadquery/cadquery
[cite: 15]
Purpose: Programmatic creation of 3D CAD models and 2D sketches via a concise Python API14.
Technology stack: Python, C++ (OCP / Open CASCADE)14.
License / SPDX: Apache-2.015.
Repository metrics: 5.6k stars, 434 forks16.
Distribution availability: PyPI (pip install cadquery), Conda-Forge14.
Performance source: Claimed by the developers (high computation speed thanks to performing boolean operations on the C++ kernel side)14.
Maintenance status: Active project16.
Limitations: No native support for AEC specifics: no DXF layers "out of the box", no mechanisms for automatically building construction dimension grids and hatching of multi-layer walls.
IfcOpenShell
An infrastructure open-source project for manipulating OpenBIM IFC standard data19.
Official link: https://github.com/ifcopenshell/ifcopenshell
[cite: 19]
Purpose: Parsing, creation, validation and geometric rendering of BIM models in IFC2x3, IFC4 and IFC4x3 formats19.
Technology stack: C++, Python, Open CASCADE (geometry evaluator)19.
License / SPDX: LGPL-3.0-or-later19.
Repository metrics: 2.7k stars, 952 forks19.
Distribution availability: PyPI (pip install ifcopenshell), Conda-Forge, C++ SDK19.
Performance source: Independently measured OpenBIM industry standard.
Maintenance status: Active project funded by the buildingSMART community19.
Limitations: The high complexity of the IFC standard requires writing custom algorithms to project 3D IfcWall and IfcWindow solids into orthogonal 2D projections for vector output to DXF.
Bonsai (formerly BlenderBIM)
A native graphical environment for authoring design in IFC format, based on Blender19.
Official link: https://github.com/ifcopenshell/ifcopenshell (part of the IfcOpenShell monorepo)19
Purpose: Creating, editing and documenting BIM models in a visual interface19.
Technology stack: Python, Blender API, IfcOpenShell19.
License / SPDX: GPL-3.0-or-later19.
Repository metrics: Merged into the unified IfcOpenShell ecosystem19.
Distribution availability: Add-on for the Blender graphical editor.
Performance source: Claimed by the developers.
Maintenance status: Active project.
Limitations: The hard dependency on the Blender environment complicates deploying the service in lightweight server containers.
ReportLab
A professional library for direct programmatic synthesis of vector PDF documents.
Official link: PyPI reportlab / https://www.reportlab.com/
Purpose: Producing multi-page vector drawing documents with precisely positioned graphic primitives and text title stamps.
Technology stack: Python, C rendering accelerators.
License / SPDX: BSD-3-Clause (ReportLab Open Source Edition).
Repository metrics: Standard component of the Python infrastructure.
Distribution availability: PyPI (pip install reportlab).
Performance source: Independently confirmed high page generation speed.
Maintenance status: Actively maintained commercial and open-source product.
Limitations: No built-in AEC drafting functions (double wall lines with automatic opening cleanup, dynamic architectural dimension ticks), which requires writing a custom vector translator.
WeasyPrint
A utility tool for converting laid-out HTML5/CSS3 documents into vector PDF format.
Official link: https://github.com/Kozea/WeasyPrint
Purpose: Converting web pages and layouts into print-ready PDF documents.
Technology stack: Python, Pango, cairo, GDF.
License / SPDX: BSD-3-Clause.
Repository metrics: 6k stars.
Distribution availability: PyPI (pip install weasyprint).
Performance source: Claimed by the developers.
Maintenance status: Active project.
Limitations: The program is designed for print layout. When generating architectural drawings at 1:100 or 1:50 scale, rounding errors arise when converting CSS pixels to typographic points, leading to micro-gaps at the joints of vector elements.
Research into AI and Algorithmic Methods for Layout Generation
Generating room topology from unstructured specifications has evolved from generative neural approaches to mathematical solvers of spatial constraint systems.
Comparative Analysis of Algorithmic Families
Each mathematical paradigm has its own characteristics regarding compliance with geometric constraints:
Generative Adversarial Networks (GAN) and Diffusion Models
Architectures such as HouseGAN20, HouseGAN++21 and Graph2Plan22 use room adjacency graphs  to generate floor topology. The HouseGAN++ model applies graph attention mechanisms (GAT) for iterative refinement of room boundaries21. Diffusion models treat the process as reverse denoising over room corner coordinates or semantic raster masks.
Input data: Room adjacency graph, outer boundary frame of the building21.
Output data: Semantic raster masks or fuzzy sets of polygons21.
Hard constraint support: Not supported. The models do not guarantee compliance with an exact target room area with a tolerance smaller than .
Geometric defects: Non-orthogonal walls, overlapping spaces and isolated enclosed zones without passages occur. The models do not account for physical wall thickness (300–400 mm for exterior and 100–120 mm for interior walls), which causes fatal offsets during automatic vectorization.
Transformers and Autoregressive Architectures
Models of the LayoutTransformer and VecFormer23 class tokenize layout elements as sequences .
Input data: A sequence of textual requirements for the room program.
Output data: A set of bounding boxes of elements23.
Hard constraint support: Limited. The models allow spatial errors to accumulate as the number of rooms grows and cannot guarantee vertical alignment of load-bearing walls across floors.
Procedural Methods and Mathematical Solvers (Constraint Solvers)
Approaches based on mixed-integer linear programming (MILP), satisfiability modulo theories (SMT / Z3 Solver) and rectangular dualization (Rectangular Dualization / REL) provide strictly deterministic compliance with the given specification.
Formalizing the spatial problem in a Constraint Solver is based on defining a system of equalities and inequalities. For each room  variables are introduced for the bottom-left corner coordinates , width  and height :
Exact match of the target area:

Room proportion constraint (Aspect Ratio):

Condition of mutual non-overlap of rooms:
For any pair of rooms  and  binary spatial separation variables are introduced :

Physical adjacency constraint:
The length of the contact line between adjacent rooms  and  must exceed the width of the door unit  taking technological offsets into account:

Analytical Registry of AI Research Projects and Datasets
HouseGAN / HouseGAN++
A generative adversarial network for iterative construction of layouts based on an adjacency graph20.
Official link: https://github.com/sepidsh/Housegan-data-reader
[cite: 21]
Purpose: Generating vector room faces from a conceptual connection graph21.
Technology stack: Python, PyTorch21.
License / SPDX: Non-commercial research license.
Dataset: RPLAN21.
Code and weights availability: Source code is available; weights are provided on request for research purposes21.
Hard constraint support: None.
Graph2Plan
A neural pipeline for layout generation that takes into account the building's outer boundary and the adjacency graph22.
Official link: https://github.com/HangZhangZ/MaskPLAN
[cite: 22]
Purpose: Searching and fitting a spatial structure to a given outer boundary contour22.
Technology stack: Python, PyTorch, C++ layout helpers22.
License / SPDX: Unverified (Research repository).
Dataset: RPLAN21.
Code and weights availability: Code available on GitHub22.
Hard constraint support: Partial. Similar variants are retrieved from a database and then deformed, which violates exact target room areas.
CubiCasa5K
The largest dataset and a baseline neural kernel for vectorizing raster building plans24.
Official link: https://github.com/CubiCasa/CubiCasa5k
[cite: 24]
Purpose: Segmentation and vectorization of floor plan images into 80+ classes of architectural objects24.
Technology stack: Python, PyTorch, OpenCV24.
License / SPDX: CC-BY-NC-4.0 (Dataset)25, MIT (Model code)25.
Repository metrics: 567 stars, 155 forks24.
Code and weights availability: Code and pretrained weights (model_best_val_loss_var.pkl) are available for download24.
Hard constraint support: Not applicable (a recognition tool, not a synthesis tool).
FloorPlanCAD
A dataset of vector CAD drawings for element symbol detection and segmentation tasks27.
Official link: https://github.com/bertjiazheng/Awesome-CAD
[cite: 27]
Purpose: Training neural systems for panoptic detection of CAD symbols27.
Technology stack: Python, JSON/CAD annotations.
License / SPDX: MIT27.
Repository metrics: 331 stars, 46 forks27.
Hard constraint support: Not applicable.
Building Code Checking (ACC), BIM Interoperability and RAG over Regulatory Documentation
Programmatic checking of spatial solutions for compliance with national norms (SP, SNiP, IBC) requires translating regulatory texts into strictly deterministic mathematical rules of a validation engine.
Architecture for Translating Regulatory Text into Deterministic Constraints
Using large language models (LLMs) directly to check the geometric correctness of drawn walls is unacceptable because of the statistical nature of LLMs and their tendency to hallucinate. Building code validation is built on a separation of responsibilities:
RAG module (Retrieval-Augmented Generation): Searches the regulatory knowledge base (e.g. SP 54.13330.2022) and extracts numeric thresholds (minimum corridor width, maximum evacuation route length, daylight factors).
Rule compiler: Converts the extracted parameters into a machine-readable structured format (JSON Schema).
Deterministic Rule Engine: Computes geometric parameters over the real vector graph of the building without LLM involvement.
The machine-readable rule format is represented by the following data structure:



JSON
{
  "rule_id": "SP_54.13330.2022_SEC_6.2",
  "target_space": "living_room",
  "constraints": {
    "min_area_sqm": 14.0,
    "min_width_m": 3.0,
    "min_window_to_floor_ratio": 0.125,
    "max_travel_distance_to_exit_m": 25.0
  }
}


The IDS Standard (Information Delivery Specification) and the ifctester Tool
In the OpenBIM ecosystem, semantic and geometric completeness is checked using the open IDS standard developed by the international buildingSMART consortium. IDS checks are executed with the open-source ifctester library, part of the IfcOpenShell project19.
ifctester performs programmatic validation of IFC files on the following parameters:
Class presence check: Confirming that all rooms are represented by IfcSpace instances and walls by IfcWallStandardCase19.
Attribute and Pset check: Validating that custom property sets are populated (Property Sets, e.g. Pset_SpaceCommon.GrossFloorArea).
Space boundary audit: Checking the correct generation of IfcRelSpaceBoundary objects linking room geometry to the enclosing wall structures.
Analysis of Existing Products and Market Gaps
The modern market for generative design software consists of both proprietary SaaS platforms and research systems.
Comparative Characteristics of Generative Architectural Platforms
Product
Software type
Input data
Output data
Cost / Model
DXF/IFC support
Geometry engine
Key limitations
Maket.ai
Proprietary SaaS
Brief text, dimensions, raster plans
2D plans, 3D renders, PDF, DXF
Freemium (from $20/mo to $100/mo)
DXF (Yes), IFC (No)
Probabilistic AI (Diffusion/GAN)
No rigorous computation of wall thickness, no code checking
TestFit
Proprietary Desktop/SaaS
Site boundaries, setbacks, Unit Mix
3D massing, parking, DXF, IFC, Revit
Commercial Enterprise ($3000–$6000/year)
DXF (Yes), IFC (Yes)
Deterministic MILP Solver
Narrow specialization in standard residential blocks and parking
Finch3D
Proprietary SaaS
3D building core, outer faces
2D/3D floor plans, IFC, Grasshopper
Commercial SaaS ($100–$250/mo)
DXF (Partial), IFC (Yes)
Graph-based procedural decomposition
Requires prior modelling of the building shape
Autodesk Forma
Proprietary SaaS
GIS site data, setbacks
3D massing, sun exposure/noise maps, IFC
Autodesk subscription ($180/mo)
DXF (Yes), IFC (Yes)
Procedural macro analysis
Limited by massing level of detail, does not generate interior rooms
Hypar
Open/Commercial
C#/Python scripts, text parameters
3D BIM models, IFC, DXF, JSON
Free tier, $25–$75/mo
DXF (Yes), IFC (Yes)
Function-oriented scripting
Requires manual development of algorithms for each new object type
Planner5D
Proprietary SaaS
Drag-and-drop, raster scans
2D/3D interiors, renders, PDF
Freemium
DXF (Limited), IFC (No)
Heuristic visual planner
Oriented toward DIY design, no engineering BIM export

Systematic Review of Products
Maket.ai
A cloud service for rapid generation of conceptual sketches of residential houses28.
Input data: Text prompts, room count parameters, target areas, uploaded plan images28.
Output data: Plan images, PDF, DXF files28.
Pricing: Free credits, then $20/mo (Homeowner) or $100/mo (Pro)29.
Target audience: Private homeowners, architects at the concept stage28.
Limitations: The program uses a probabilistic neural model28. In the exported DXF files, walls are uncoordinated polygons that ignore the real physical thickness of load-bearing structures and service ducts28. There is no building code checking.
TestFit
An industrial configurator for buildings, parking and master plans based on optimization algorithms.
Input data: Urban planning setbacks, site boundary, number of storeys, apartment typology.
Output data: Construction-grade 3D volumes, floor layouts, export to DXF, IFC and native integration with Autodesk Revit.
Pricing: Industrial B2B SaaS (from $3000 to $6000 per seat per year).
Target audience: Developers, designers of residential neighbourhoods.
Limitations: The system's engine targets hard-coded typologies (corridor-type residential sections, parking). The system cannot process an arbitrary text prompt with complex non-orthogonal interior architecture.
Finch3D
A cloud system for automatically fitting layout solutions inside a given 3D building envelope.
Input data: Volumetric building boundaries, target room areas.
Output data: Apartment distribution topology, native export to Rhino/Grasshopper and IFC.
Pricing: From $100 to $250 per user per month.
Target audience: Large architectural firms.
Limitations: The system does not work "from scratch" from the brief text; the exact 3D geometry of the building's outer volume must be uploaded first.
Hypar
An open design automation platform using serverless functions in C# and Python33.
Input data: Parameters from the web interface, user scripts33.
Output data: Valid IFC and DXF files, 3D models33.
Pricing: Free basic tier, $25–$75/mo for professionals33.
Target audience: BIM automation engineers, computational designers33.
Limitations: There is no ready "out-of-the-box" AI generator; the system requires programming the generation logic for each building type.
Identified Market Gaps
Gap between the AI interface and engineering precision: Existing AI tools (Maket.ai) offer high flexibility of text input but output a "picture" unsuitable for construction28. Professional tools (TestFit) offer high engineering precision but require manual entry of hundreds of numeric parameters.
No end-to-end building code validation at the generation stage: None of the products dynamically reformulates solver constraints based on the texts of regulatory documents (RAG over Building Codes).
Recommended Architecture and Technology Stack
To implement a stable AI layout configurator that precisely follows the specification, a hybrid four-layer architecture is recommended.
Layers of the Hybrid System
Specification parsing and user interaction layer (LLM Layer)
Technology stack: FastAPI, Python 3.11, Pydantic v2, the instructor library to ensure strict typing of LLM output (OpenAI GPT-4o / Claude 3.5 Sonnet).
Purpose: Converting the user's unstructured brief text into a deterministic JSON specification object (list of rooms, target areas, adjacency coefficients).
Topology mathematical optimization layer (Constraint Solver Engine)
Technology stack: Google OR-Tools (CP-SAT and MILP modules), the Z3 Theorem Prover library, the Shapely computational library.
Purpose: Exact calculation of point coordinates of rectangular or polygonal room blocks based on equations of adjacency, areas and boundary impermeability.
Construction geometry and BIM layer (Procedural CAD/BIM Engine)
Technology stack: CadQuery / Open CASCADE (for 3D operations), ezdxf (for 2D DXF generation)1, IfcOpenShell (for IFC4 generation)19.
Purpose: Giving the room point blocks real construction properties (applying exterior and interior wall layers, cutting window and door openings, building layer tables).
Rendering and documentation layer (Export & Render Engine)
Technology stack: ReportLab (for direct creation of 1:100 vector PDFs with a title block), Three.js / WebGL (for the browser viewer).
Purpose: Producing the final drawing package and an interactive 3D model.
Delimiting the Scope of LLM Use
Within the proposed architecture, strict boundaries are set on the use of large language models:
Permitted uses of LLMs:
Extracting parameters from brief texts and minutes of client meetings.
Running the conversational interface ("Move the kitchen closer to the balcony").
Semantic search over regulatory documents (RAG) to select the appropriate JSON constraint profile.
Strictly prohibited uses of LLMs:
Calculating numeric coordinate values () of wall corners.
Computing geometric intersections and thicknesses of structures.
Making the final decision on the project's compliance with building codes.
Directly generating the text content of DXF or IFC files.
MVP (Minimum Viable Product) Development Roadmap
The implementation plan for the minimum viable product is designed for 24 weeks of a regular engineering cycle:
Stage 1: Development of the specification parser and data schema (Weeks 1–4)
Designing a validatable JSON schema for the input building specification.
Creating a FastAPI service with LLM integration via the structured output subsystem (instructor).
Building end-to-end unit tests to check the accuracy of extracting room parameters from unstructured briefs.
Stage 2: Building the mathematical optimization core (Weeks 5–10)
Formulating the MILP/CP-SAT mathematical problem for placing rectangular room blocks.
Integrating the Shapely library for computing boolean spatial operations.
Implementing a module for automatic allocation of corridors and circulation paths.
Stage 3: Building the vector 2D documentation generator (Weeks 11–16)
Integrating the ezdxf library to build the drawing layer structure (A-WALL, A-DOOR, A-WIND, A-ANNO-DIMS)1.
Developing procedural modules for cleaning up wall junctions and placing dynamic door and window blocks.
Creating a vector PDF rendering subsystem based on ReportLab with support for title blocks (stamps) per ISO/GOST standards.
Stage 4: Integration of OpenBIM, the code validator and the user UI (Weeks 17–24)
Developing a module for translating topology into IFC4 format using IfcOpenShell19.
Integrating ifctester for automatic checking of the resulting model against IDS scenarios19.
Creating a React and Three.js front-end for 2D editing and 3D viewing of the generated variants.
Validation Strategy
Every generated layout variant passes through an automated four-stage quality-check pipeline.



                      [ Initial layout ]
                              │
                              ▼
┌────────────────────────────────────────────────────────────┐
│ 1. Geometric validation (Shapely / Open CASCADE)           │
│    • Polygon self-intersection check (is_valid)            │
│    • Area match check: SUM(A_rooms) == A_total             │
└──────────────┬─────────────────────────────────────────────┘
               │ Success
               ▼
┌────────────────────────────────────────────────────────────┐
│ 2. Building code check (ACC Rules Engine)                  │
│    • Corridor passage width >= 1.2 m                       │
│    • Evacuation route distance <= R_max                    │
└──────────────┬─────────────────────────────────────────────┘
               │ Success
               ▼
┌────────────────────────────────────────────────────────────┐
│ 3. Syntax validation of DXF and IFC structures             │
│    • ezdxf.audit() (Layer and block reference check)       │
│    • ifctester IDS Audit (IFC4 schema validity check)      │
└──────────────┬─────────────────────────────────────────────┘
               │ Success
               ▼
┌────────────────────────────────────────────────────────────┐
│ 4. Graphical check of the vector PDF                       │
│    • Viewport clipping and title block integrity check     │
└────────────────────────────────────────────────────────────┘


1. Geometric validation
The constructed wall polygons are checked with the Shapely polygon.is_valid method for absence of self-intersections and degenerate edges. Absolute equality of areas is checked: the sum of room areas plus the area of wall thicknesses must equal the total building area measured by the outer contour:

2. Building code validation (Code Compliance)
A visibility graph (Visibility Graph) is automatically built inside the floor geometry, and the shortest path from any point of the rooms to an evacuation exit is computed using Dijkstra's method. The distance must not exceed the limit value  specified in the regulatory file.
3. Syntax and structure check of DXF and IFC files
DXF Audit: Calling ezdxf.audit(doc) to detect broken references to layer tables, missing object handles and unclosed contours1.
IFC Audit: Calling ifctester to match the generated data structure against the normative IDS XML file19.
Key DEAD ZONES
When designing the architecture of the AI configurator, the following dead-end engineering approaches should be taken into account:
Dead zone 1: Attempting to train an end-to-end neural network to directly generate DXF/DWG bytes.
The structure of CAD files is strictly vectorized and syntactically sensitive. Errors at the level of individual bytes make files unreadable in AutoCAD and Revit. Neural networks are not capable of stably reproducing rigid binary specifications.
Dead zone 2: Delegating geometric computation and code checking to a language model.
Language models have no built-in mechanism for computing spatial coordinates and give false confirmations of passing code checks on incorrect drawings.
Dead zone 3: Using GPL-licensed libraries (LibreDWG, Bonsai) inside a proprietary cloud SaaS. Directly importing or linking GPLv3-licensed code into a closed commercial service entails legal risks of forced disclosure of the source code of the entire platform5. All GPL components must be moved into isolated microservices that communicate via a network REST/gRPC API.
Dead zone 4: Generating floor plans without accounting for vertical alignment in multi-storey buildings.
Computing each floor independently leads to load-bearing walls of the second floor hanging over open spaces of the first floor. The optimization solver must compute all floors simultaneously in a single vertical grid of axes (Structural Grid).
Three Strongest Product Opportunities
Based on the technology and market analysis, the following key product directions stand out:
1. B2B SaaS for express site testing (Automated Site Test-Fit Engine)
An automated platform for investment developers that takes the GIS coordinates of a land plot and the desired parameters of the building volume. Within minutes the system generates not just an abstract 3D massing but a worked-out floor-by-floor layout with a drawn structure of apartments, passages and service shafts, with ready export to DXF and IFC.
2. Optimization core API infrastructure for BIM systems (Generative Layout API)
A headless API service for AEC software developers that allows integrating a "generate a layout from the brief requirements" function directly into the interfaces of Autodesk Revit, Archicad or Renga.
3. Intelligent drawing auditor and fixer (AI Blueprint Code-Checker & Fixer)
A B2B tool for automatically checking engineering drawings and BIM models for compliance with national norms. The system not only finds geometric violations but also uses a mathematical Constraint Solver to automatically correct the position of partitions and produce an updated drawing package in DXF/PDF format.
Sources
mozman/ezdxf: Python interface to DXF - GitHub, https://github.com/mozman/ezdxf
GitHub - aka863/ezdxf: dxf library, https://github.com/aka863/ezdxf
DatacloudIntl/dc_ezdxf: DataCloud's fork of ezdxf - GitHub, https://github.com/DatacloudIntl/dc_ezdxf
conda-forge/ezdxf-feedstock - GitHub, https://github.com/conda-forge/ezdxf-feedstock
GitHub - h4ck3rm1k3/libredwg, https://github.com/h4ck3rm1k3/libredwg
h4ck3rm1k3/LibreDWGCPlusPlus: Compiling LIbreDWG with G++, https://github.com/h4ck3rm1k3/LibreDWGCPlusPlus
Official mirror of libredwg. With CI hooks and nightly releases. PR's ok, https://github.com/libredwg/libredwg
conda-forge/libredwg-feedstock - GitHub, https://github.com/conda-forge/libredwg-feedstock
FreeCAD/src/Mod/Draft/importDWG.py at main - GitHub, https://github.com/FreeCAD/FreeCAD/blob/master/src/Mod/Draft/importDWG.py
FreeCAD/src/Mod/Draft/Draft.py at main - GitHub, https://github.com/FreeCAD/FreeCAD/blob/master/src/Mod/Draft/Draft.py
FreeCAD/src/Mod/Part/App/TopoShape.cpp at main - GitHub, https://github.com/FreeCAD/FreeCAD/blob/master/src/Mod/Part/App/TopoShape.cpp
FreeCAD/src/Mod/Robot/RobotExample.py at main - GitHub, https://github.com/FreeCAD/FreeCAD/blob/master/src/Mod/Robot/RobotExample.py
GitHub - Open-Cascade-SAS/OCCT-samples-csharp, https://github.com/Open-Cascade-SAS/OCCT-samples-csharp
GitHub - bweissinger/cadquery-2.0: A parametric CAD scripting, https://github.com/bweissinger/cadquery-2.0
CadQuery/cadquery: A python parametric CAD scripting ... - GitHub, https://github.com/cadquery/cadquery
CadQuery repositories - GitHub, https://github.com/orgs/CadQuery/repositories
cadquery.github.io, https://github.com/CadQuery/cadquery.github.io
GitHub - bernhard-42/jupyter-cadquery: An extension to render, https://github.com/bernhard-42/jupyter-cadquery
IfcOpenShell/IfcOpenShell: Open source IFC library and ... - GitHub, https://github.com/ifcopenshell/ifcopenshell
Computer-Aided Layout Generation for Building Design: A Review, https://www.researchgate.net/publication/390772434_Computer-Aided_Layout_Generation_for_Building_Design_A_Review
House-GAN++ (data-reader) - GitHub, https://github.com/sepidsh/Housegan-data-reader
implementation of “MaskPLAN: Masked Generative Layout ... - GitHub, https://github.com/HangZhangZ/MaskPLAN
GitHub - WesKwong/VecFormer: [NeurIPS 2025] Official, https://github.com/WesKwong/VecFormer
CubiCasa5k floor plan dataset - GitHub, https://github.com/CubiCasa/CubiCasa5k
GitHub - mageaustralia/FloorPlanAnalyzer: Experimental floor plan, https://github.com/mageaustralia/FloorPlanAnalyzer
CubiCasa5k/LICENSE at master - GitHub, https://github.com/CubiCasa/CubiCasa5k/blob/master/LICENSE
A list of awesome Computer-Aided Design (CAD) papers - GitHub, https://github.com/bertjiazheng/Awesome-CAD
Maket AI: все о нейросети в одном обзоре (English: Maket AI: everything about the neural network in one review), https://room-design.ai/maket-ai.php
Maket Pricing & Plans: Start Free, https://www.maket.ai/pricing
Maket.ai, https://www.maket.ai/
How to Use Maket: Design Your Home from Idea to 3D, https://www.maket.ai/blog/how-to-use-maket
Maket: Pricing, Features & Alternatives (2026) - Toolhunter, https://toolhunter.ai/ai-tool/maket
Generative Space Planning - AI Floor Plan Design | Hypar, https://datadrivenaec.com/tools/hypar
Hypar.io - design by algorithms - AEC Magazine, https://aecmag.com/technology/hypar-io/
Hypar Reviews - 2026 - Slashdot, https://slashdot.org/software/p/Hypar/
