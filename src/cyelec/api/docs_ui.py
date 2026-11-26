"""Zero-CDN, GDPR-safe User Documentation & Cyprus BS 7671 / EAC Engineering Know-How Portal."""
# ruff: noqa: E501

from __future__ import annotations

from cyelec import __version__


def render_workbench_guide_tab_html() -> str:
    """Return compact Quick-Reference & Know-How HTML embedded inside the Workbench tab."""
    return """
          <div style="display:flex;justify-content:space-between;align-items:center;
                      flex-wrap:wrap;gap:8px;margin-bottom:12px;padding-bottom:8px;
                      border-bottom:1px solid var(--border);">
            <div>
              <strong style="color:var(--navy);font-size:13.5px;">
                CyElec User Documentation &amp; Cyprus BS 7671 / EAC Know-How
              </strong>
              <div style="color:var(--muted);font-size:11.5px;">
                Quick operational guide right inside the workbench — zero external CDNs.
              </div>
            </div>
            <div style="display:flex;gap:8px;">
              <a href="/guide" target="_blank" rel="noopener"
                 class="btn btn-primary btn-sm" style="text-decoration:none;">
                Open Full Searchable Guide &amp; Formulas ↗
              </a>
              <a href="/docs" target="_blank" rel="noopener"
                 class="btn btn-secondary btn-sm" style="text-decoration:none;">
                REST API Reference (/docs) ↗
              </a>
            </div>
          </div>

          <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));
                      gap:12px;">
            <div class="panel" style="margin:0;">
              <h2>1. 5-Step ETEK Engineer Workflow</h2>
              <ol style="margin:6px 0 0 16px;padding:0;line-height:1.55;font-size:12px;">
                <li>Select a Cyprus benchmark or upload an architectural <code>.dxf</code>/<code>.ifc</code>.</li>
                <li>If layers are non-standard, map them in the <strong>DXF Layer Mapping Wizard</strong>.</li>
                <li>Set <strong>EAC Profile</strong> (<code>18th+A2</code> 2.5 m or <code>17th+A3</code> 3.0 m),
                    <strong>TT earthing</strong>, PV kVA, and firm/ETEK branding.</li>
                <li>Click <strong>Run Deterministic Solve</strong>, then refine visually on the
                    <strong>Interactive Floorplan</strong> (click rooms to reclassify, click
                    <em>Relocate DB1</em> to re-route conduits) or override phases/Cg per circuit.</li>
                <li>Resolve any <strong>BLOCKER</strong> flags, save revisions in
                    <strong>Workspace</strong>, and click <strong>Download EAC ZIP Pack</strong>.</li>
              </ol>
            </div>

            <div class="panel" style="margin:0;">
              <h2>2. Cyprus EAC &amp; BS 7671 Key Limits</h2>
              <ul style="margin:6px 0 0 16px;padding:0;line-height:1.55;font-size:12px;">
                <li><strong>Ambient (Method B masonry):</strong> 40°C indoor (<code>Ca=0.87</code>),
                    45°C roof/outdoor (<code>Ca=0.79</code>) for 70°C PVC Cu.</li>
                <li><strong>Voltage Drop (cumulative):</strong> <code>&le; 3.0%</code> lighting,
                    <code>&le; 5.0%</code> power (sub-main + final combined).</li>
                <li><strong>TT Earthing (Cyprus default):</strong>
                    <code>Ra &times; I&Delta;n &le; 50 V</code>, 30 mA RCD mandatory on finals,
                    100 mA Type S selective RCD on TT sub-mains (Reg 536.4.1.4).</li>
                <li><strong>Wet Zones (Sec 701):</strong> 2.5 m socket exclusion (18th+A2) or
                    3.0 m (17th+A3), clipped to bathroom walls.</li>
                <li><strong>EAC Escalations:</strong> 3P mandatory for houses/2-3 bed flats/PV &gt; 5 kVA;
                    CT metering &gt; 100 A/ph; Substation warn &ge; 100 kVA, blocker &ge; 150 kVA.</li>
              </ul>
            </div>

            <div class="panel" style="margin:0;">
              <h2>3. Messy DXF &amp; Bilingual Know-How</h2>
              <ul style="margin:6px 0 0 16px;padding:0;line-height:1.55;font-size:12px;">
                <li><strong>AutoCAD Blocks:</strong> Nested <code>INSERT</code> blocks are exploded
                    recursively with Layer <code>"0"</code> inheritance &amp; fixture hints.</li>
                <li><strong>Missing <code>A-ROOM</code>?</strong> Closed rooms are automatically
                    derived via wall-to-room polygonization.</li>
                <li><strong>EN / EL / Greeklish labels:</strong> Recognizes <code>ΣΑΛΟΝΙ</code>,
                    <code>SALONI</code>, <code>ΚΟΥΖΙΝΑ</code>, <code>KOUZ.</code>,
                    <code>ΥΠΝΟΔΩΜΑΤΙΟ</code>, <code>YPN. 1</code>, <code>ΜΠΑΝΙΟ</code>,
                    <code>MPANIO</code>, <code>ΒΕΡΑΝΤΑ</code>, <code>ΠΛΥΣΤΑΡΙΟ</code>.</li>
                <li><strong>Multi-Floor DXFs:</strong> Combine <code>GF.dxf + 1F.dxf</code> via
                    <code>POST /v1/solve/dxf/multifloor</code> or
                    <code>cyelec solve-multifloor</code>.</li>
              </ul>
            </div>

            <div class="panel" style="margin:0;">
              <h2>4. Custom BOQ, Tender Mode &amp; Governance</h2>
              <ul style="margin:6px 0 0 16px;padding:0;line-height:1.55;font-size:12px;">
                <li><strong>Custom Firm Rate Card:</strong> Save your firm's EUR unit rates &amp;
                    5% reduced VAT once via <code>PUT /v1/tenants/me</code> (<code>boq_rate_overrides</code>).</li>
                <li><strong>Unpriced Tender BOQ:</strong> Set <code>tender_boq_blank_rates=true</code>
                    (or <code>--tender-boq</code>) to leave Excel Column G blank while preserving
                    live contractor bid auto-calc formulas.</li>
                <li><strong>Statutory Gate:</strong> All outputs carry SHA-256 provenance and require
                    formal review &amp; stamp by a licensed <strong>ETEK Electrical Engineer</strong>.</li>
                <li><strong>EU GDPR:</strong> Export all tenant data via
                    <code>GET /v1/tenants/me/export</code> or erase completely via
                    <code>DELETE /v1/tenants/me</code>.</li>
              </ul>
            </div>
          </div>
"""


def render_user_guide_html(base_url: str = "") -> str:
    """Return full standalone searchable User Documentation & Engineering Know-How page."""
    import html

    prefix = base_url.rstrip("/")
    og_img = html.escape(f"{prefix}/og-thumbnail.jpg", quote=True)
    logo_url = html.escape(f"{prefix}/logo.png", quote=True)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <meta name="theme-color" content="#1b365d" />
  <meta
    name="description"
    content="CyElec User Documentation &amp; BS 7671 / IEC 60364 / Cyprus EAC Engineering Know-How."
  />
  <meta property="og:type" content="article" />
  <meta property="og:title" content="CyElec — User Guide &amp; Cyprus Electrical Know-How" />
  <meta
    property="og:description"
    content="Complete ETEK engineer playbook: DXF prep, BS 7671/EAC formulas, BOQ, multi-floor &amp; API."
  />
  <meta property="og:image" content="{og_img}" />
  <meta property="og:image:width" content="1200" />
  <meta property="og:image:height" content="630" />
  <meta name="twitter:card" content="summary_large_image" />
  <meta name="twitter:image" content="{og_img}" />
  <link rel="icon" type="image/svg+xml" href="/favicon.svg" />
  <link rel="icon" type="image/x-icon" href="/favicon.ico" />
  <link rel="apple-touch-icon" href="{logo_url}" />
  <title>CyElec — User Documentation &amp; Engineering Know-How</title>
  <style>
    :root {{
      --navy: #1b365d;
      --navy-dark: #0f2342;
      --accent: #0284c7;
      --amber: #d97706;
      --bg: #f4f7fa;
      --card: #ffffff;
      --border: #d9e2ec;
      --text: #102a43;
      --muted: #486581;
      --code-bg: #f0f4f8;
    }}
    * {{ box-sizing: border-box; }}
    html {{ scroll-behavior: smooth; }}
    body {{
      margin: 0;
      font-family: "Noto Sans", "DejaVu Sans", system-ui, -apple-system, sans-serif;
      background: var(--bg);
      color: var(--text);
      font-size: 13.5px;
      line-height: 1.6;
    }}
    header {{
      background: var(--navy);
      color: #ffffff;
      padding: 10px 22px;
      display: flex;
      justify-content: space-between;
      align-items: center;
      border-bottom: 3px solid var(--navy-dark);
      position: sticky;
      top: 0;
      z-index: 20;
    }}
    .brand-lockup {{
      display: flex;
      align-items: center;
      gap: 10px;
      text-decoration: none;
      color: #ffffff;
    }}
    .brand-logo {{
      width: 34px;
      height: 34px;
      border-radius: 7px;
      border: 1px solid rgba(56, 189, 248, 0.45);
      display: block;
    }}
    header h1 {{ margin: 0; font-size: 15.5px; letter-spacing: 0.2px; }}
    .nav-links {{ display: flex; gap: 10px; align-items: center; }}
    .nav-btn {{
      color: #ffffff;
      text-decoration: none;
      font-size: 12px;
      font-weight: 600;
      padding: 6px 12px;
      border-radius: 5px;
      border: 1px solid rgba(255, 255, 255, 0.25);
      background: rgba(255, 255, 255, 0.08);
    }}
    .nav-btn:hover {{ background: rgba(255, 255, 255, 0.18); }}
    .nav-btn-primary {{
      background: #0284c7;
      border-color: #38bdf8;
    }}
    .shell {{
      max-width: 1280px;
      margin: 0 auto;
      display: grid;
      grid-template-columns: 260px 1fr;
      gap: 20px;
      padding: 20px;
    }}
    @media (max-width: 900px) {{
      .shell {{ grid-template-columns: 1fr; }}
      nav.toc {{ position: static; }}
    }}
    nav.toc {{
      position: sticky;
      top: 72px;
      align-self: start;
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 14px;
    }}
    .toc-title {{
      font-size: 11px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.5px;
      color: var(--muted);
      margin: 0 0 8px 0;
    }}
    .search-box {{
      width: 100%;
      padding: 7px 9px;
      border: 1px solid var(--border);
      border-radius: 5px;
      font-size: 12px;
      margin-bottom: 10px;
    }}
    nav.toc a {{
      display: block;
      padding: 5px 8px;
      margin: 2px 0;
      color: var(--text);
      text-decoration: none;
      border-radius: 4px;
      font-size: 12.5px;
      font-weight: 500;
    }}
    nav.toc a:hover {{ background: #ebf4ff; color: var(--navy); }}
    article {{
      display: flex;
      flex-direction: column;
      gap: 18px;
    }}
    section.doc-card {{
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 20px 24px;
      box-shadow: 0 1px 2px rgba(16, 42, 67, 0.04);
      scroll-margin-top: 72px;
    }}
    section.doc-card.hidden {{ display: none; }}
    h2 {{
      margin: 0 0 10px 0;
      font-size: 17px;
      color: var(--navy);
      border-bottom: 2px solid #e2e8f0;
      padding-bottom: 6px;
    }}
    h3 {{
      margin: 14px 0 6px 0;
      font-size: 14px;
      color: #243b53;
    }}
    p {{ margin: 6px 0 10px 0; }}
    ul, ol {{ margin: 6px 0 12px 20px; padding: 0; }}
    li {{ margin: 4px 0; }}
    table {{
      width: 100%;
      border-collapse: collapse;
      margin: 10px 0 12px 0;
      font-size: 12.5px;
    }}
    th, td {{
      border: 1px solid var(--border);
      padding: 7px 10px;
      text-align: left;
      vertical-align: top;
    }}
    th {{
      background: var(--navy);
      color: #ffffff;
      font-weight: 600;
    }}
    tbody tr:nth-child(even) {{ background: #f8fafc; }}
    code {{
      font-family: "DejaVu Sans Mono", ui-monospace, monospace;
      font-size: 12px;
      background: var(--code-bg);
      padding: 1px 5px;
      border-radius: 3px;
      color: #0f2342;
    }}
    pre {{
      background: #0f2342;
      color: #e2e8f0;
      padding: 12px 14px;
      border-radius: 6px;
      overflow-x: auto;
      font-size: 12px;
      line-height: 1.5;
      margin: 8px 0 12px 0;
    }}
    pre code {{
      background: transparent;
      color: inherit;
      padding: 0;
    }}
    .callout {{
      padding: 10px 14px;
      border-radius: 6px;
      margin: 10px 0;
      font-size: 12.5px;
      border-left: 4px solid var(--accent);
      background: #f0f9ff;
    }}
    .callout-warn {{
      border-left-color: var(--amber);
      background: #fffbeb;
    }}
    .callout-danger {{
      border-left-color: #c53030;
      background: #fff5f5;
    }}
    .badge {{
      display: inline-block;
      padding: 2px 7px;
      border-radius: 3px;
      font-size: 11px;
      font-weight: 700;
    }}
    .b-blocker {{ background: #f8d7da; color: #721c24; }}
    .b-warn {{ background: #fff3cd; color: #856404; }}
    .b-info {{ background: #d9e2ec; color: #1b365d; }}
    .hero-banner {{
      width: 100%;
      max-height: 220px;
      object-fit: cover;
      border-radius: 8px;
      border: 1px solid var(--border);
      display: block;
    }}
    footer {{
      text-align: center;
      color: var(--muted);
      font-size: 11.5px;
      padding: 14px 20px 28px;
    }}
  </style>
</head>
<body>
  <header>
    <a class="brand-lockup" href="/">
      <img src="/logo.png" alt="CyElec" class="brand-logo" width="34" height="34" />
      <h1>CyElec — User Documentation &amp; Cyprus EAC Engineering Know-How</h1>
    </a>
    <div class="nav-links">
      <a class="nav-btn nav-btn-primary" href="/">&larr; Open Workbench</a>
      <a class="nav-btn" href="/docs">OpenAPI (/docs)</a>
      <span style="font-size:11px;opacity:0.85;">v{__version__}</span>
    </div>
  </header>

  <div class="shell">
    <nav class="toc" aria-label="Documentation navigation">
      <div class="toc-title">Filter Topics</div>
      <input
        id="guideSearch"
        class="search-box"
        type="search"
        placeholder="Search e.g. TT, 2.5 m, BOQ, PV, GDPR..."
        oninput="filterGuide(this.value)"
      />
      <div class="toc-title">Chapters</div>
      <a href="#quickstart">1. 5-Minute Quickstart</a>
      <a href="#dxf-prep">2. Architect DXF &amp; Bilingual Know-How</a>
      <a href="#bs7671-formulas">3. BS 7671:2018+A2 &amp; Cyprus Formulas</a>
      <a href="#eac-supply">4. EAC Supply Tiering &amp; MILP Balance</a>
      <a href="#boq-tender">5. Deliverables, Custom BOQ &amp; Tender Mode</a>
      <a href="#etek-governance">6. ETEK Review Flags &amp; Sign-Off Gate</a>
      <a href="#saas-api-cli">7. Multi-Tenant SaaS, CLI &amp; EU GDPR</a>
    </nav>

    <article id="guideContent">
      <img
        src="/og-thumbnail.jpg"
        alt="CyElec — BS 7671 • IEC 60364 • Cyprus EAC Electrical Pipeline"
        class="hero-banner"
      />

      <section id="quickstart" class="doc-card">
        <h2>1. 5-Minute Quickstart: From Architect DXF to EAC Submission Pack</h2>
        <p>
          <strong>CyElec</strong> is a 100% deterministic electrical engineering pipeline built for
          <strong>BS 7671</strong>, <strong>IEC 60364</strong>, and
          <strong>Electricity Authority of Cyprus (EAC)</strong> plan-approval deliverables — with
          zero commercial CAD licenses and zero external CDNs.
        </p>
        <ol>
          <li>
            <strong>Choose Input:</strong> Start with one of the 4 built-in Cyprus benchmarks
            (3-Bed Apartment, Detached Villa PV+EV, 2-Storey Villa <code>MDB &rarr; DB-GF/DB-1F</code>,
            Commercial Office) or upload any R12&ndash;R2018 architectural <code>.dxf</code> (or
            <code>.ifc</code>) floorplan.
          </li>
          <li>
            <strong>Map Non-Standard Layers (if needed):</strong> On upload, CyElec automatically
            inspects DXF layers (<code>POST /v1/dxf/layers</code>) and opens the
            <strong>DXF Layer Mapping Wizard</strong> so you can map custom architect layer names
            (e.g., <code>01_TOIXOI</code> &rarr; <code>walls</code>) without editing the CAD file.
          </li>
          <li>
            <strong>Configure Cyprus Design Basis:</strong> Select the EAC profile
            (<code>cyprus_eac_18th_a2</code> with 2.5 m bathroom socket exclusion or
            <code>cyprus_eac_17th_a3</code> with 3.0 m), earthing system (Cyprus default
            <strong>TT</strong>), ambient temperature (<code>40&deg;C</code> indoor /
            <code>45&deg;C</code> outdoor), rooftop PV kVA, and your firm &amp; ETEK registration details.
          </li>
          <li>
            <strong>Review &amp; Apply Visual Pass-2 Overrides:</strong>
            <ul>
              <li>In <strong>Interactive Floorplan (SVG)</strong>: click any room polygon to
                  reclassify it (<code>room_type_overrides</code>) or click
                  <em>Relocate DB1 on Floorplan</em> to move the distribution board and live-reroute
                  all NetworkX conduits.</li>
              <li>In <strong>Circuits &amp; Pass-2 Overrides</strong>: lock individual circuit phases
                  (<code>L1/L2/L3</code>) or grouping counts (<code>Cg</code>) and re-solve instantly.</li>
            </ul>
          </li>
          <li>
            <strong>Save Revisions &amp; Export:</strong> Save snapshots in the
            <strong>Workspace &amp; Revision Diff</strong> tab (CyElec automatically carries your
            Pass-2 overrides forward from <code>Rev 1</code> to <code>Rev 2</code> when the architect
            issues a new drawing!), verify all flags in <strong>ETEK Review Flags</strong>, and click
            <strong>Download EAC ZIP Pack</strong>.
          </li>
        </ol>
      </section>

      <section id="dxf-prep" class="doc-card">
        <h2>2. Architect DXF Preparation &amp; Bilingual EN / EL / Greeklish Know-How</h2>
        <p>
          Real Cyprus architectural drawings rarely follow textbook CAD naming. CyElec is engineered
          to ingest both clean and messy DXFs deterministically:
        </p>
        <table>
          <thead>
            <tr>
              <th>Role</th>
              <th>Preferred Layer Names</th>
              <th>Automatic Resilience Built In</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td><strong>Rooms</strong></td>
              <td><code>A-ROOM</code>, <code>ROOMS</code>, <code>SPACE</code></td>
              <td>If closed room polylines are missing, CyElec automatically derives room polygons
                  from closed wall/door loops via <code>shapely.ops.polygonize</code> and logs
                  <code>ROOMS_POLYGONIZED_FROM_WALLS</code>.</td>
            </tr>
            <tr>
              <td><strong>Walls / Doors / Windows</strong></td>
              <td><code>A-WALL</code>, <code>A-DOOR</code>, <code>A-WIND</code></td>
              <td>Recursive AutoCAD <code>INSERT</code> block explosion with Layer <code>"0"</code>
                  inheritance; auto-scales <code>mm</code>, <code>cm</code>, or <code>m</code> via
                  DXF <code>$INSUNITS</code> header.</td>
            </tr>
            <tr>
              <td><strong>Plumbing Fixtures</strong></td>
              <td><code>A-FIXT</code>, <code>SANITARY</code>, <code>PLUMBING</code></td>
              <td>Detects bathtubs and shower trays by layer OR exploded block-name hints
                  (<code>BATH</code>, <code>SHOWER</code>) to generate BS 7671 Section 701 wet zones.</td>
            </tr>
            <tr>
              <td><strong>DB Anchor</strong></td>
              <td><code>A-DB</code>, <code>E-DB</code></td>
              <td>Detects <code>DB1</code> / <code>MDB</code> blocks or text; falls back cleanly to
                  corridor/entry wall with an audit flag if omitted.</td>
            </tr>
          </tbody>
        </table>

        <h3>Bilingual Room Labels (Deterministic Tier-1 Lookup)</h3>
        <p>
          Room text is Unicode NFD accent-stripped and case-folded, matching English, Greek, and
          Cyprus architectural Greeklish abbreviations without any LLM guesswork:
        </p>
        <table>
          <thead>
            <tr>
              <th>Normalized Type</th>
              <th>Recognized English / Greek / Greeklish Examples</th>
              <th>Default Cyprus Circuit Template</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td><code>living_room</code></td>
              <td>Living Room, Lounge, <code>ΣΑΛΟΝΙ</code>, <code>SALONI</code>, <code>ΚΑΘΙΣΤΙΚΟ</code></td>
              <td>Lighting + General 32 A Ring + Dedicated 16 A Curve C Split A/C</td>
            </tr>
            <tr>
              <td><code>kitchen</code></td>
              <td>Kitchen, <code>ΚΟΥΖΙΝΑ</code>, <code>KOUZINA</code>, <code>KOUZ.</code>, <code>KOYZINA</code></td>
              <td>Kitchen 32 A Ring + 32 A Cooker + 16 A Oven + Dishwasher radial</td>
            </tr>
            <tr>
              <td><code>kitchen_living</code></td>
              <td>Open-Plan Kitchen/Living, <code>ΣΑΛΟΝΟΚΟΥΖΙΝΑ</code>, <code>SALONOKOUZINA</code></td>
              <td>Combined Kitchen + Living loads + Dedicated Split A/C</td>
            </tr>
            <tr>
              <td><code>bedroom</code> / <code>master_bedroom</code></td>
              <td>Bedroom, <code>ΥΠΝΟΔΩΜΑΤΙΟ</code>, <code>YPN. 1</code>, <code>ΚΡΕΒΑΤΟΚΑΜΑΡΑ</code></td>
              <td>Lighting + General Ring sockets + Dedicated 16 A Curve C Split A/C</td>
            </tr>
            <tr>
              <td><code>bathroom</code> / <code>ensuite</code></td>
              <td>Bathroom, Shower, <code>ΜΠΑΝΙΟ</code>, <code>MPANIO</code>, <code>WC/NTOYS</code></td>
              <td>Section 701 Wet Zones clipped to walls; no 13 A sockets inside 2.5 m / 3.0 m zone</td>
            </tr>
            <tr>
              <td><code>utility</code> / <code>veranda</code></td>
              <td>Utility, Laundry, <code>ΠΛΥΣΤΑΡΙΟ</code>, Veranda, <code>ΒΕΡΑΝΤΑ</code>, <code>ΜΠΑΛΚΟΝΙ</code></td>
              <td>Utility: Washing machine + Immersion/Solar heater; Veranda: 45&deg;C outdoor derating</td>
            </tr>
          </tbody>
        </table>

        <h3>Multi-Floor Buildings (<code>MDB &rarr; DB-GF / DB-1F</code>)</h3>
        <p>
          Combine separate floor DXF files (e.g., <code>GF.dxf</code> and <code>1F.dxf</code>) into
          one unified multi-board model with automatic sub-main feeder sizing and cumulative
          &Delta;V enforcement:
        </p>
        <pre><code>cyelec solve-multifloor --floor GF=ground.dxf --floor 1F=first.dxf --out-dir out/villa_pack</code></pre>
      </section>

      <section id="bs7671-formulas" class="doc-card">
        <h2>3. BS 7671:2018+A2 &amp; Cyprus EAC Deterministic Engineering Basis</h2>
        <p>
          Every calculation is backed by versioned YAML standards tables in <code>standards/</code>
          and recorded in the SHA-256 provenance block of every output artifact.
        </p>
        <table>
          <thead>
            <tr>
              <th>Verification Check</th>
              <th>BS 7671 / EAC Rule</th>
              <th>Deterministic Formula &amp; Cyprus Defaults</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td><strong>Ambient &amp; Grouping Derating</strong></td>
              <td>Appendix 4 (Tables 4B1, 4C1, 52.2)</td>
              <td>Cyprus Method B (conduit in masonry): <code>Ca = 0.87</code> at 40&deg;C indoor,
                  <code>Ca = 0.79</code> at 45&deg;C outdoor/roof (70&deg;C PVC Cu). Effective
                  capacity: <code>Iz = It &times; Ca &times; Cg &times; Ci &ge; In</code>.</td>
            </tr>
            <tr>
              <td><strong>32 A Ring Final Circuits</strong></td>
              <td>Reg 433.1.204</td>
              <td><code>2.5 mm&sup2;</code> ring legs must satisfy <code>Iz &ge; 20 A</code>
                  (fails if grouped heavily at 40&deg;C &rarr; auto-flagged / upsized); voltage drop
                  evaluated at <code>L / 4</code>. Floor area capped at <code>&le; 100 m&sup2;</code>/ring.</td>
            </tr>
            <tr>
              <td><strong>Voltage Drop (&Delta;V)</strong></td>
              <td>Appendix 4, Sec 6.4</td>
              <td><code>&Delta;V% &le; 3.0%</code> lighting, <code>&le; 5.0%</code> power. On
                  multi-floor boards, CyElec enforces <strong>cumulative</strong>
                  <code>&Delta;V_submain + &Delta;V_final &le; 3% / 5%</code> automatically.</td>
            </tr>
            <tr>
              <td><strong>Earth Fault Protection (TT vs TN)</strong></td>
              <td>Reg 411.5.3 / Table 41.3</td>
              <td><strong>Cyprus TT default:</strong> <code>Ra &times; I&Delta;n &le; 50 V</code>,
                  warning if <code>Ra &gt; 200 &Omega;</code>, BLOCKER if any TT circuit lacks RCD.
                  TT sub-mains enforce <strong>100 mA Type S time-delayed RCD</strong> (Reg 536.4.1.4).
                  <strong>TN-S / TN-C-S:</strong> <code>Zs = Ze + (R1+R2)&times;1.20 &le; Zs,max</code>
                  (<code>U0 = 230 V, Cmin = 0.95</code>).</td>
            </tr>
            <tr>
              <td><strong>Adiabatic CPC Sizing</strong></td>
              <td>Reg 543.1.3</td>
              <td><code>S_min = &radic;(I&sup2;t) / k</code> using device let-through energy and
                  <code>k = 115</code> (70&deg;C PVC) or <code>k = 143</code> (90&deg;C XLPE).</td>
            </tr>
            <tr>
              <td><strong>BS 7671:2018+A2 Checks</strong></td>
              <td>Regs 421.1.7, 434.5.1, 530.3.201, 551.7.2</td>
              <td>
                &bull; <strong>PV &gt; 0 kVA:</strong> Flags bidirectional RCBO / busbar backfeed check.<br/>
                &bull; <strong>Prospective Fault Current (<code>Ipf</code>):</strong> Warns for
                10 kA switchgear when <code>Ipf &gt; 6 kA</code>; raises <strong>BLOCKER</strong> if
                <code>Ipf &gt; 10 kA</code>.<br/>
                &bull; <strong>AFDD Advisory:</strong> Emitted under <code>cyprus_eac_18th_a2</code>
                for residential/HRRB socket circuits.
              </td>
            </tr>
            <tr>
              <td><strong>EV Charging &amp; Wet Zones</strong></td>
              <td>Sections 722 &amp; 701</td>
              <td>Dedicated EV radial requires Type A RCD + 6 mA DC RDC-DD (or Type B) &amp;
                  PME/TT earthing check. Bathroom sockets strictly excluded inside <code>2.5 m</code>
                  (<code>18th+A2</code>) or <code>3.0 m</code> (<code>17th+A3</code>) of bath/shower.</td>
            </tr>
          </tbody>
        </table>
      </section>

      <section id="eac-supply" class="doc-card">
        <h2>4. EAC Supply Tiering, Escalations &amp; Exact MILP 3-Phase Balancing</h2>
        <ul>
          <li><strong>Mandatory 3-Phase (EAC Rules):</strong> Independent houses, 2&ndash;3 bedroom
              apartments, offices, shops, workshops, industrial premises, or any installation with
              rooftop PV <code>&gt; 5.0 kVA</code>.</li>
          <li><strong>Exact MILP 3-Phase Load Balancer:</strong> Uses <code>scipy.optimize.milp</code>
              (HiGHS) with lossless symmetry breaking (<code>I_L1 &ge; I_L2 &ge; I_L3</code>) to
              minimize <code>max(I_p) - min(I_p)</code> across <code>L1 / L2 / L3</code> while
              respecting engineer-locked phases. Imbalance <code>&gt; 10%</code> raises
              <code>PHASE_IMBALANCE_EXCEEDS_TARGET</code>. Large multi-floor boards automatically
              accept feasible incumbents with a deterministic LPT fallback.</li>
          <li><strong>EAC Escalation Thresholds:</strong>
            <ul>
              <li><code>&gt; 100 A / phase</code> &rarr; <strong>CT Metering Enclosure Required</strong>
                  (added automatically to BOQ &amp; SLD).</li>
              <li><code>&ge; 100 kVA</code> diversified &rarr; <strong>Substation Warning</strong>.</li>
              <li><code>&ge; 150 kVA</code> diversified &rarr; <strong>Substation Consultation BLOCKER</strong>
                  (mandatory early EAC Distribution Network consultation).</li>
            </ul>
          </li>
        </ul>
      </section>

      <section id="boq-tender" class="doc-card">
        <h2>5. Deliverables Pack, Custom BOQ Rate Cards &amp; Unpriced Tender BOQ</h2>
        <p>Every <code>Download EAC ZIP Pack</code> / <code>cyelec solve-eac</code> run emits:</p>
        <table>
          <thead>
            <tr><th>Artifact</th><th>Purpose</th></tr>
          </thead>
          <tbody>
            <tr>
              <td><code>layout_electrical.dxf</code></td>
              <td>R2018 CAD overlay (<code>E-LITE</code>, <code>E-POWR</code>, <code>E-AC</code>,
                  <code>E-COND</code>, <code>E-WETZ</code>, <code>E-DB</code>, <code>E-TITLE</code>).</td>
            </tr>
            <tr>
              <td><code>single_line_diagram.svg</code> / <code>.dxf</code> / <code>.qet</code></td>
              <td>Hierarchical SLD (<code>MDB &rarr; DB-GF/DB-1F</code>, Type S RCDs, PV/EV, firm/ETEK
                  titleblock) plus QElectroTech XML project.</td>
            </tr>
            <tr>
              <td><code>load_schedule.xlsx</code> &amp; <code>.csv</code></td>
              <td>3-sheet live-formula workbook: <em>Load Schedule</em> (live <code>=SUMIF</code>
                  3-phase balance), <em>EAC &amp; Review Flags</em>, and <em>Bill of Quantities</em>
                  (live <code>=ROUND(F*G,2)</code> &amp; Cyprus VAT formulas).</td>
            </tr>
            <tr>
              <td><code>calc_report.html</code> / <code>.md</code> &amp; <code>eac_submission_pack.html</code></td>
              <td>Print-ready A4-landscape engineering calculation report &amp; EAC cover pack
                  (English, Greek <code>EL</code>, or Bilingual <code>EN / EL</code>; optional native PDF via <code>--pdf</code>).</td>
            </tr>
            <tr>
              <td><code>manifest.json</code> &amp; <code>design_result.json</code></td>
              <td>Full SHA-256 integrity manifest and machine-readable snapshot for audit &amp; revision diffing.</td>
            </tr>
          </tbody>
        </table>

        <div class="callout">
          <strong>Custom Firm Rate Cards &amp; 5% Cyprus Reduced VAT:</strong> Save your consulting
          practice's EUR unit rates once via <code>PUT /v1/tenants/me</code> (field
          <code>boq_rate_overrides</code>, including <code>vat_rate: 0.05</code> for eligible primary
          residences). Every subsequent solve for your tenant automatically inherits your rates.
        </div>
        <div class="callout callout-warn">
          <strong>Unpriced Tender BOQ Mode (Contractor Bidding):</strong> Pass
          <code>--tender-boq</code> on CLI or <code>"tender_boq_blank_rates": true</code> in
          <code>DesignOverrides</code>. CyElec leaves Column G (<em>Unit Rate EUR</em>) blank and
          highlighted in Excel while keeping every <code>=ROUND(F*G,2)</code>, <code>=SUM(...)</code>,
          VAT, and Grand Total formula live so contractors just type their bid rates and submit!
        </div>
      </section>

      <section id="etek-governance" class="doc-card">
        <h2>6. ETEK Review Flags Register &amp; Statutory Sign-Off Gate</h2>
        <div class="callout callout-danger">
          <strong>Statutory Responsibility Notice:</strong> CyElec automates deterministic BS 7671
          and Cyprus EAC calculations to eliminate manual spreadsheet and drafting errors, but it
          does <em>not</em> replace statutory professional accountability. Every deliverable requires
          formal verification, signature, and stamp by a licensed <strong>ETEK Electrical Engineer</strong>
          prior to EAC submission.
        </div>
        <table>
          <thead>
            <tr><th>Severity</th><th>Meaning</th><th>Required Action</th></tr>
          </thead>
          <tbody>
            <tr>
              <td><span class="badge b-blocker">BLOCKER</span></td>
              <td>Non-compliant design or mandatory authority escalation (e.g., TT circuit without
                  RCD, <code>Ipf &gt; 10 kA</code>, Substation &ge; 150 kVA, thermal/Zs fail).</td>
              <td><strong>Must be resolved</strong> via engineering override or EAC consultation
                  before issuing plans for approval.</td>
            </tr>
            <tr>
              <td><span class="badge b-warn">WARNING</span></td>
              <td>Engineering judgment required (e.g., 3P imbalance &gt; 10%, <code>Ra &gt; 200 &Omega;</code>,
                  <code>Ipf &gt; 6 kA</code> requiring 10 kA switchgear, PV backfeed check).</td>
              <td>Review calculation note and verify on site / with switchgear manufacturer data.</td>
            </tr>
            <tr>
              <td><span class="badge b-info">INFO</span></td>
              <td>Audit &amp; provenance trace (e.g., <code>ETEK_SIGN_OFF_REQUIRED</code>,
                  <code>ROOMS_POLYGONIZED_FROM_WALLS</code>, room/DB override applied).</td>
              <td>Retained in the permanent revision audit trail.</td>
            </tr>
          </tbody>
        </table>
      </section>

      <section id="saas-api-cli" class="doc-card">
        <h2>7. Multi-Tenant EU SaaS, API Keys, CLI &amp; GDPR Lifecycle</h2>
        <ul>
          <li><strong>API Authentication:</strong> On Production, every <code>/v1/*</code> request
              requires a per-tenant <code>X-API-Key</code> (<code>cyelec_live_&lt;8hex&gt;_&lt;secret&gt;</code>,
              verified via <code>O(1)</code> prefix-indexed salted PBKDF2-SHA256). Paste your key into
              the Workbench sidebar — it is stored in browser <code>sessionStorage</code> only and
              cleared automatically when you close the tab.</li>
          <li><strong>Pass-2 Override Carry-Forward &amp; Diff:</strong> When uploading <code>Rev 2</code>
              to an existing project (<code>POST /v1/projects/{{id}}/revisions/dxf</code> with
              <code>carry_forward=true</code>), your previous room reclassifications, DB location,
              and circuit overrides carry forward automatically, and
              <code>GET /v1/projects/{{id}}/diff/1/2</code> highlights every room, circuit, kVA, and
              BOQ delta.</li>
          <li><strong>EU GDPR Rights (Articles 17 &amp; 20):</strong>
            <ul>
              <li><code>GET /v1/tenants/me/export</code> — Downloads your complete portable JSON
                  bundle (profile, projects, revisions, usage — zero API key secrets).</li>
              <li><code>DELETE /v1/projects/{{project_id}}</code> — Atomically deletes a single
                  project, all its revisions, and gzipped cloud snapshots.</li>
              <li><code>DELETE /v1/tenants/me</code> — Cascade-erases all tenant data across
                  Firestore &amp; EU Cloud Storage and immediately revokes the tenant's API key.</li>
            </ul>
          </li>
        </ul>

        <h3>Quick CLI &amp; REST Examples</h3>
        <pre><code># Generate a branded, bilingual EAC submission pack with unpriced Tender BOQ
cyelec solve-eac plan.dxf --out-dir out/eac_pack \\
  --language bilingual --tender-boq \\
  --firm-name "Kypros MEP Ltd" --engineer "N. Papadopoulos" --etek-reg "ETEK-E-4821"

# Combine Ground &amp; First Floor DXFs into MDB -&gt; DB-GF / DB-1F
cyelec solve-multifloor --floor GF=ground.dxf --floor 1F=first.dxf --out-dir out/villa

# Save custom firm BOQ rates (incl. 5% reduced primary-residence VAT) via REST API
curl -sS -X PUT https://&lt;your-cyelec-host&gt;/v1/tenants/me \\
  -H "X-API-Key: $CYELEC_KEY" -H "Content-Type: application/json" \\
  -d '{{"display_name":"Kypros MEP Ltd","boq_rate_overrides":{{"vat_rate":0.05,"switchgear_eur":{{"rcbo_30ma_1p":44.0}}}}}}'</code></pre>
      </section>
    </article>
  </div>

  <footer>
    CyElec v{__version__} &bull; Deterministic BS 7671 / IEC 60364 / Cyprus EAC Engineering Pipeline
    &bull; Zero Commercial CAD Licenses &bull; EU GDPR Compliant
  </footer>

  <script>
    function filterGuide(raw) {{
      const q = (raw || '').trim().toLowerCase();
      document.querySelectorAll('section.doc-card').forEach(sec => {{
        const match = !q || sec.textContent.toLowerCase().includes(q);
        sec.classList.toggle('hidden', !match);
      }});
    }}
  </script>
</body>
</html>
"""  # nosec B608
