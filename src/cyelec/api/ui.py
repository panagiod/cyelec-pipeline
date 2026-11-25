"""Zero-dependency, GDPR-safe Interactive ETEK Engineer Review & Visual Pass-2 Workbench UI."""

from __future__ import annotations

from cyelec import __version__


def render_etek_workbench_html() -> str:
    """Return self-contained HTML/CSS/JS ETEK Engineer Review Dashboard served at GET /."""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>cyelec-pipeline — ETEK Engineer Review &amp; Pass-2 Workbench</title>
  <style>
    :root {{
      --navy: #1b365d;
      --navy-light: #2b4c7e;
      --bg: #f4f7fa;
      --card: #ffffff;
      --border: #d9e2ec;
      --text: #102a43;
      --muted: #627d98;
      --pass-bg: #d4edda; --pass-fg: #155724;
      --warn-bg: #fff3cd; --warn-fg: #856404;
      --fail-bg: #f8d7da; --fail-fg: #721c24;
      --info-bg: #d9e2ec; --info-fg: #1b365d;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "Noto Sans", "DejaVu Sans", system-ui, -apple-system, sans-serif;
      background: var(--bg);
      color: var(--text);
      font-size: 13px;
    }}
    header {{
      background: var(--navy);
      color: #ffffff;
      padding: 12px 20px;
      display: flex;
      justify-content: space-between;
      align-items: center;
      border-bottom: 3px solid #0f2342;
    }}
    header h1 {{ margin: 0; font-size: 16px; letter-spacing: 0.2px; }}
    header .meta {{ font-size: 11px; opacity: 0.85; }}
    .layout {{
      display: grid;
      grid-template-columns: 320px 1fr;
      gap: 16px;
      padding: 16px;
      min-height: calc(100vh - 54px);
    }}
    .panel {{
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 14px;
      box-shadow: 0 1px 2px rgba(16,42,67,0.04);
    }}
    .panel h2 {{
      margin: 0 0 10px 0;
      font-size: 12.5px;
      color: var(--navy);
      text-transform: uppercase;
      letter-spacing: 0.4px;
      border-bottom: 1px solid var(--border);
      padding-bottom: 6px;
    }}
    label {{
      display: block;
      font-weight: 600;
      margin: 8px 0 3px;
      font-size: 11px;
      color: var(--muted);
    }}
    input, select {{
      width: 100%;
      padding: 6px 8px;
      border: 1px solid var(--border);
      border-radius: 4px;
      font-size: 12px;
      background: #fff;
    }}
    .btn {{
      display: inline-block;
      width: 100%;
      padding: 8px 12px;
      border: none;
      border-radius: 4px;
      font-weight: 700;
      cursor: pointer;
      margin-top: 8px;
      font-size: 12px;
    }}
    .btn-sm {{ width: auto; padding: 5px 10px; margin-top: 0; font-size: 11.5px; }}
    .btn-primary {{ background: var(--navy); color: #fff; }}
    .btn-primary:hover {{ background: var(--navy-light); }}
    .btn-secondary {{ background: #e2e8f0; color: var(--navy); }}
    .btn-secondary:hover {{ background: #cbd5e0; }}
    .btn-active {{ background: #c53030; color: #fff; }}
    .kpi-grid {{
      display: grid;
      grid-template-columns: repeat(5, 1fr);
      gap: 10px;
      margin-bottom: 14px;
    }}
    .kpi {{
      background: var(--card);
      border: 1px solid var(--border);
      border-left: 4px solid var(--navy);
      border-radius: 6px;
      padding: 9px 11px;
    }}
    .kpi .k {{ font-size: 10px; color: var(--muted); text-transform: uppercase; }}
    .kpi .v {{ font-size: 14px; font-weight: 700; color: var(--navy); margin-top: 3px; }}
    .tabs {{
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      margin-bottom: 12px;
      border-bottom: 2px solid var(--border);
    }}
    .tab {{
      padding: 8px 13px;
      cursor: pointer;
      font-weight: 600;
      color: var(--muted);
      border-bottom: 2px solid transparent;
      margin-bottom: -2px;
      background: none;
      border-top: none;
      border-left: none;
      border-right: none;
      font-size: 12px;
    }}
    .tab.active {{ color: var(--navy); border-bottom-color: var(--navy); }}
    .tab-pane {{ display: none; }}
    .tab-pane.active {{ display: block; }}
    .fp-toolbar {{
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 8px;
      background: #f8fafc;
      border: 1px solid var(--border);
      border-radius: 4px;
      padding: 8px 10px;
      margin-bottom: 8px;
      font-size: 11.5px;
    }}
    .svg-box {{
      background: #fff;
      border: 1px solid var(--border);
      border-radius: 4px;
      padding: 8px;
      overflow: auto;
      max-height: 70vh;
    }}
    .svg-box.db-mode svg {{ cursor: crosshair; }}
    .svg-box svg {{ width: 100%; height: auto; display: block; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 11.5px; }}
    th, td {{ border: 1px solid var(--border); padding: 5px 7px; text-align: left; }}
    th {{ background: var(--navy); color: #fff; }}
    tbody tr:nth-child(even) {{ background: #f8fafc; }}
    .r {{ text-align: right; font-variant-numeric: tabular-nums; }}
    .c {{ text-align: center; }}
    .badge {{
      display: inline-block;
      padding: 2px 6px;
      border-radius: 3px;
      font-size: 10px;
      font-weight: 700;
    }}
    .b-pass {{ background: var(--pass-bg); color: var(--pass-fg); }}
    .b-warn {{ background: var(--warn-bg); color: var(--warn-fg); }}
    .b-fail {{ background: var(--fail-bg); color: var(--fail-fg); }}
    .b-info {{ background: var(--info-bg); color: var(--info-fg); }}
    .bar-row {{
      display: grid;
      grid-template-columns: 44px 1fr 76px;
      align-items: center;
      gap: 8px;
      margin: 5px 0;
    }}
    .bar-track {{ background: #e2e8f0; height: 14px; border-radius: 3px; overflow: hidden; }}
    .bar-fill {{ height: 100%; }}
    .status-line {{ font-size: 11.5px; color: var(--muted); margin-top: 8px; }}
    .mini-input {{ width: 66px; padding: 3px 4px; font-size: 11px; }}
    .layer-box {{
      margin-top: 8px;
      padding: 8px;
      background: #f8fafc;
      border: 1px solid var(--border);
      border-radius: 4px;
      display: none;
    }}
    .layer-row {{
      display: grid;
      grid-template-columns: 1fr 94px;
      gap: 6px;
      align-items: center;
      margin: 4px 0;
      font-size: 11px;
    }}
  </style>
</head>
<body>
  <header>
    <h1>cyelec-pipeline — ETEK Engineer Review &amp; Visual Pass-2 Workbench</h1>
    <div class="meta">v{__version__} | BS 7671 / IEC 60364 / Cyprus EAC | EU B2B SaaS</div>
  </header>

  <div class="layout">
    <aside class="panel">
      <h2>1. Input &amp; DXF Layer Wizard</h2>
      <label for="tenantId">Tenant ID (X-Tenant-ID)</label>
      <input id="tenantId" value="cy-etek-demo" />

      <label for="apiKeyInput">Tenant API Key (X-API-Key, session only)</label>
      <input id="apiKeyInput" type="password" placeholder="Optional: cyelec_live_..." />

      <label for="benchmarkSel">Cyprus Benchmark Fixture</label>
      <select id="benchmarkSel">
        <option value="apartment_3bed">3-Bed Cyprus Apartment (120 m²)</option>
        <option value="villa_pv_ev">Detached Villa + 7.5 kVA PV + 7.4 kW EV (216 m²)</option>
        <option value="villa_2storey">
          2-Storey Villa 228 m² (MDB -&gt; DB-GF &amp; DB-1F)
        </option>
        <option value="commercial_office">
          Commercial Office 450 m² (CT Metering &amp; Substation)
        </option>
      </select>

      <label for="dxfFile">Or Upload Architectural DXF (.dxf / .ifc)</label>
      <input id="dxfFile" type="file" accept=".dxf,.ifc" />
      <div id="layerWizard" class="layer-box">
        <strong style="font-size:11px;color:#1b365d;">DXF Layer Mapping Wizard</strong>
        <div id="layerRows"></div>
      </div>

      <h2 style="margin-top:14px;">2. BS 7671 &amp; EAC Overrides</h2>
      <label for="eacProfile">EAC / BS 7671 Profile</label>
      <select id="eacProfile">
        <option value="cyprus_eac_18th_a2">cyprus_eac_18th_a2 (2.5 m Bath Excl)</option>
        <option value="cyprus_eac_17th_a3">cyprus_eac_17th_a3 (3.0 m Bath Excl)</option>
      </select>

      <label for="earthing">Earthing System</label>
      <select id="earthing">
        <option value="TT">TT (Cyprus EAC Default)</option>
        <option value="TN-S">TN-S</option>
        <option value="TN-C-S">TN-C-S</option>
      </select>

      <label for="zeRa">Ze / Ra (Ω, optional)</label>
      <input id="zeRa" type="number" step="0.1" placeholder="Default (TT=80, TN-S=0.8)" />

      <label for="ambientC">Global Ambient Override (°C, optional)</label>
      <input id="ambientC" type="number" step="1" placeholder="Default (40°C / 45°C)" />

      <label for="pvKva">PV Capacity Override (kVA, optional)</label>
      <input id="pvKva" type="number" step="0.5" placeholder="Use model default" />

      <h2 style="margin-top:14px;">3. Branding &amp; Language</h2>
      <label for="reportLang">Deliverable Language</label>
      <select id="reportLang">
        <option value="en">English (EN)</option>
        <option value="el">Ελληνικά (EL)</option>
        <option value="bilingual">Bilingual (EN / EL)</option>
      </select>
      <label for="firmName">Consulting Firm Name</label>
      <input id="firmName" placeholder="e.g., Kypros MEP Consulting Ltd" />
      <label for="engName">ETEK Engineer Name</label>
      <input id="engName" placeholder="e.g., N. Papadopoulos" />
      <label for="etekReg">ETEK Registration No.</label>
      <input id="etekReg" placeholder="e.g., ETEK-E-4821" />

      <button class="btn btn-primary" onclick="runSolve()">Run Deterministic Solve</button>
      <button class="btn btn-secondary" onclick="downloadBundle()">Download EAC ZIP Pack</button>
      <div id="statusLine" class="status-line">Ready. Click Run Deterministic Solve.</div>
    </aside>

    <main>
      <div class="kpi-grid">
        <div class="kpi">
          <div class="k">Recommended EAC Supply</div><div class="v" id="kSupply">-</div>
        </div>
        <div class="kpi">
          <div class="k">Diversified / Connected</div><div class="v" id="kKva">-</div>
        </div>
        <div class="kpi">
          <div class="k">3-Phase Balance / Conduit</div><div class="v" id="kBal">-</div>
        </div>
        <div class="kpi">
          <div class="k">Indicative BOQ (incl. VAT)</div><div class="v" id="kBoq">-</div>
        </div>
        <div class="kpi">
          <div class="k">ETEK Review Gate</div><div class="v" id="kFlags">-</div>
        </div>
      </div>

      <div class="panel">
        <div class="tabs">
          <button class="tab active" onclick="openTab('tabCircuits', this)">
            Circuits &amp; Pass-2 Overrides
          </button>
          <button class="tab" onclick="openTab('tabFloorplan', this)">
            Interactive Floorplan (SVG)
          </button>
          <button class="tab" onclick="openTab('tabSld', this)">
            Single-Line Diagram (SVG)
          </button>
          <button class="tab" onclick="openTab('tabBoq', this)">
            Bill of Quantities (EUR)
          </button>
          <button class="tab" onclick="openTab('tabFlags', this)">ETEK Review Flags</button>
          <button class="tab" onclick="openTab('tabWorkspace', this); loadProjects();">
            Workspace &amp; Revision Diff
          </button>
        </div>

        <div id="tabCircuits" class="tab-pane active">
          <div id="balanceBars" style="margin-bottom:14px;"></div>
          <div id="subMainsBox" style="margin-bottom:14px;"></div>
          <div style="overflow-x:auto;">
            <table>
              <thead>
                <tr>
                  <th>ID</th><th>Board</th><th>Description</th><th>Phase (Override)</th><th>Cg</th>
                  <th class="r">Ib (A)</th><th>Device</th><th class="c">Cable/CPC</th>
                  <th class="r">L (m)</th><th class="r">Iz/Req</th><th class="r">ΔV (%)</th>
                  <th class="c">Status</th>
                </tr>
              </thead>
              <tbody id="circuitsBody"></tbody>
            </table>
          </div>
        </div>

        <div id="tabFloorplan" class="tab-pane">
          <div class="fp-toolbar">
            <span><strong>Visual Pass-2 Editor:</strong> Click any room to reclassify, or</span>
            <button id="btnDbMode" class="btn btn-secondary btn-sm" onclick="toggleDbMode()">
              Relocate DB1 on Floorplan
            </button>
            <button class="btn btn-secondary btn-sm" onclick="resetVisualOverrides()">
              Reset Visual Overrides
            </button>
            <span id="roomEditBox" style="display:none;gap:6px;align-items:center;">
              <strong id="roomEditLbl"></strong>
              <select id="roomTypeSel" style="width:150px;"></select>
              <button class="btn btn-primary btn-sm" onclick="applyRoomOverride()">
                Apply &amp; Re-Solve
              </button>
            </span>
          </div>
          <div id="floorplanSvg" class="svg-box">Run a solve to render floorplan overlay.</div>
        </div>

        <div id="tabSld" class="tab-pane">
          <div id="sldSvg" class="svg-box">Run a solve to render Single-Line Diagram.</div>
        </div>

        <div id="tabBoq" class="tab-pane">
          <div style="overflow-x:auto;">
            <table>
              <thead>
                <tr>
                  <th>Item</th><th>Category</th><th>Description</th><th class="c">Unit</th>
                  <th class="r">Qty</th><th class="r">Rate (EUR)</th><th class="r">Total (EUR)</th>
                </tr>
              </thead>
              <tbody id="boqBody"></tbody>
            </table>
          </div>
        </div>

        <div id="tabFlags" class="tab-pane">
          <table>
            <thead>
              <tr>
                <th>Severity</th><th>Code</th><th>Category</th>
                <th>Circuit</th><th>Regulation</th><th>Details</th>
              </tr>
            </thead>
            <tbody id="flagsBody"></tbody>
          </table>
        </div>

        <div id="tabWorkspace" class="tab-pane">
          <div class="fp-toolbar">
            <input id="projName" style="width:200px;" placeholder="New Project Name" />
            <button class="btn btn-primary btn-sm" onclick="createAndSaveRevision()">
              Save as New Revision
            </button>
            <select id="projectSel" style="width:220px;" onchange="loadRevisions()"></select>
            <button class="btn btn-secondary btn-sm" onclick="diffLatestRevisions()">
              Diff Latest 2 Revisions
            </button>
            <span id="wsStatus" style="color:var(--muted);"></span>
          </div>
          <div id="revListBox" style="margin-bottom:12px;"></div>
          <div id="revDiffBox"></div>
        </div>
      </div>
    </main>
  </div>

  <script>
    const ROOM_TYPES = [
      'living_room', 'kitchen', 'kitchen_living', 'master_bedroom', 'bedroom',
      'bathroom', 'ensuite', 'wc', 'corridor', 'utility', 'veranda', 'storage'
    ];
    const LAYER_ROLES = ['auto', 'rooms', 'walls', 'doors', 'windows', 'fixtures', 'text', 'db'];

    const circuitOverrides = {{}};
    const roomTypeOverrides = {{}};
    let dbLocationOverride = null;
    let dbRelocateMode = false;
    let selectedRoomId = null;
    const layerRoleChoices = {{}};

    function escapeHtml(value) {{
      return String(value == null ? '' : value)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
    }}

    function openTab(id, btn) {{
      document.querySelectorAll('.tab-pane').forEach(p => p.classList.remove('active'));
      document.querySelectorAll('.tab').forEach(b => b.classList.remove('active'));
      document.getElementById(id).classList.add('active');
      btn.classList.add('active');
    }}

    function toggleDbMode() {{
      dbRelocateMode = !dbRelocateMode;
      const btn = document.getElementById('btnDbMode');
      const box = document.getElementById('floorplanSvg');
      btn.className = dbRelocateMode ? 'btn btn-active btn-sm' : 'btn btn-secondary btn-sm';
      btn.textContent = dbRelocateMode
        ? 'Click Floorplan to Place DB1 (Cancel)'
        : 'Relocate DB1 on Floorplan';
      box.classList.toggle('db-mode', dbRelocateMode);
    }}

    function resetVisualOverrides() {{
      Object.keys(roomTypeOverrides).forEach(k => delete roomTypeOverrides[k]);
      dbLocationOverride = null;
      selectedRoomId = null;
      document.getElementById('roomEditBox').style.display = 'none';
      if (dbRelocateMode) toggleDbMode();
      runSolve();
    }}

    function applyRoomOverride() {{
      if (!selectedRoomId) return;
      roomTypeOverrides[selectedRoomId] = document.getElementById('roomTypeSel').value;
      document.getElementById('roomEditBox').style.display = 'none';
      runSolve();
    }}

    function collectLayerMapping() {{
      const mapping = {{
        rooms: [], walls: [], doors: [], windows: [], fixtures: [], text: [], db: []
      }};
      let anyExplicit = false;
      for (const [lname, role] of Object.entries(layerRoleChoices)) {{
        if (role && role !== 'auto' && mapping[role]) {{
          mapping[role].push(lname);
          anyExplicit = true;
        }}
      }}
      return anyExplicit ? mapping : null;
    }}

    function collectOverrides() {{
      const ov = {{
        eac_profile: document.getElementById('eacProfile').value,
        earthing_system: document.getElementById('earthing').value,
        report_language: document.getElementById('reportLang').value,
        circuit_overrides: circuitOverrides,
        room_type_overrides: roomTypeOverrides
      }};
      const ze = document.getElementById('zeRa').value;
      if (ze) ov.ze_or_ra_ohm = parseFloat(ze);
      const amb = document.getElementById('ambientC').value;
      if (amb) ov.ambient_temp_c = parseInt(amb, 10);
      const pv = document.getElementById('pvKva').value;
      if (pv) ov.pv_capacity_kva = parseFloat(pv);
      if (dbLocationOverride) ov.db_location_override_mm = dbLocationOverride;

      const firm = document.getElementById('firmName').value.trim();
      const eng = document.getElementById('engName').value.trim();
      const etek = document.getElementById('etekReg').value.trim();
      if (firm || eng || etek) {{
        ov.branding = {{ firm_name: firm, engineer_name: eng, etek_reg_no: etek }};
      }}
      return ov;
    }}

    function setCircuitOverride(cid, field, val) {{
      if (!circuitOverrides[cid]) circuitOverrides[cid] = {{ circuit_id: cid }};
      if (val === '' || val === 'AUTO') {{
        delete circuitOverrides[cid][field];
      }} else {{
        circuitOverrides[cid][field] = (field === 'grouping_count') ? parseInt(val, 10) : val;
      }}
      if (Object.keys(circuitOverrides[cid]).length <= 1) delete circuitOverrides[cid];
    }}

    function renderResult(data) {{
      const d = data.result.design;
      const sp = data.result.spatial;
      const eac = d.eac_compliance;
      const pb = d.phase_balance;
      const boq = d.boq;

      document.getElementById('kSupply').textContent = eac.recommended_supply_tier;
      const divKva = eac.total_diversified_kva.toFixed(2);
      const conKva = eac.total_connected_kva.toFixed(2);
      document.getElementById('kKva').textContent = `${{divKva}} / ${{conKva}} kVA`;
      document.getElementById('kBal').textContent = pb
        ? `${{pb.imbalance_pct.toFixed(2)}}% | ${{sp.total_conduit_m.toFixed(1)}} m`
        : `1P | ${{sp.total_conduit_m.toFixed(1)}} m`;
      document.getElementById('kBoq').textContent = boq
        ? `€${{boq.grand_total_eur.toLocaleString('en-US', {{minimumFractionDigits:2}})}}`
        : '-';

      const blk = d.review_flags.filter(f => f.severity === 'blocker').length;
      const wrn = d.review_flags.filter(f => f.severity === 'warning').length;
      const inf = d.review_flags.filter(f => f.severity === 'info').length;
      document.getElementById('kFlags').textContent = `${{blk}} Blk / ${{wrn}} Wrn / ${{inf}} Info`;

      if (pb) {{
        const mx = Math.max(pb.max_phase_current_a, 1);
        const colors = {{ L1: '#8B4513', L2: '#1A202C', L3: '#718096' }};
        document.getElementById('balanceBars').innerHTML = ['L1','L2','L3'].map(p => {{
          const val = pb.phase_currents_a[p];
          const pct = Math.min(100, (val / mx) * 100);
          const st = `width:${{pct}}%;background:${{colors[p]}};`;
          const bar = `<div class="bar-fill" style="${{st}}"></div>`;
          return `<div class="bar-row"><strong>${{p}}</strong>
            <div class="bar-track">${{bar}}</div>
            <span class="r">${{val.toFixed(2)}} A</span></div>`;
        }}).join('');
      }} else {{
        document.getElementById('balanceBars').innerHTML = '';
      }}

      if (d.sub_mains && d.sub_mains.length > 0) {{
        const rows = d.sub_mains.map(sm => `<tr>
          <td><strong>${{escapeHtml(sm.board_id)}}</strong></td>
          <td>${{escapeHtml(sm.parent_board_id)}}</td>
          <td class="c">${{escapeHtml(sm.floor)}}</td>
          <td>${{escapeHtml(sm.device_label)}}</td>
          <td class="c">${{sm.cable_mm2}}/${{sm.cpc_mm2}} mm² (${{sm.length_m}} m)</td>
          <td class="r">${{sm.voltage_drop_pct.toFixed(2)}}%</td>
          <td class="r">${{sm.cumulative_lighting_vd_pct.toFixed(2)}}% /
            ${{sm.cumulative_power_vd_pct.toFixed(2)}}%</td>
        </tr>`).join('');
        document.getElementById('subMainsBox').innerHTML = `<table>
          <thead><tr>
            <th>Sub-Board</th><th>Parent</th><th class="c">Floor</th><th>Upstream Protection</th>
            <th class="c">Sub-Main Cable</th><th class="r">Sub ΔV</th>
            <th class="r">Cumul. L/P ΔV</th>
          </tr></thead><tbody>${{rows}}</tbody></table>`;
      }} else {{
        document.getElementById('subMainsBox').innerHTML = '';
      }}

      document.getElementById('circuitsBody').innerHTML = d.circuits.map(c => {{
        const s = c.spec;
        const cid = escapeHtml(s.circuit_id);
        const bid = escapeHtml(s.board_id || 'DB1');
        const desc = escapeHtml(s.description);
        const devLbl = escapeHtml(c.device_label);
        const phVal = escapeHtml(c.phase);
        const ov = circuitOverrides[s.circuit_id] || {{}};
        const curPhase = ov.phase || 'AUTO';
        const curCg = Number(ov.grouping_count || s.grouping_count);
        const phSelect = s.is_three_phase ? '3P' : `
          <select class="mini-input" data-cid="${{cid}}" data-field="phase">
            <option value="AUTO" ${{curPhase==='AUTO'?'selected':''}}>Auto (${{phVal}})</option>
            <option value="L1" ${{curPhase==='L1'?'selected':''}}>L1</option>
            <option value="L2" ${{curPhase==='L2'?'selected':''}}>L2</option>
            <option value="L3" ${{curPhase==='L3'?'selected':''}}>L3</option>
          </select>`;
        const cgInput = `<input class="mini-input" type="number" min="1" max="20"
          data-cid="${{cid}}" data-field="grouping_count" value="${{curCg}}" />`;
        const bCls = c.overall_pass ? 'b-pass' : 'b-fail';
        return `<tr>
          <td><strong>${{cid}}</strong></td>
          <td><code>${{bid}}</code></td>
          <td>${{desc}}</td>
          <td>${{phSelect}}</td>
          <td>${{cgInput}}</td>
          <td class="r">${{s.design_current_ib_a.toFixed(2)}}</td>
          <td>${{devLbl}}</td>
          <td class="c">${{c.cable_mm2}}/${{c.cpc_mm2}}</td>
          <td class="r">${{s.length_m.toFixed(1)}}</td>
          <td class="r">${{c.iz_derated_a.toFixed(1)}}/${{c.required_iz_a.toFixed(1)}}</td>
          <td class="r">${{c.voltage_drop_pct.toFixed(2)}}%</td>
          <td class="c"><span class="badge ${{bCls}}">${{c.overall_pass?'PASS':'FAIL'}}</span></td>
        </tr>`;
      }}).join('');

      document.getElementById('floorplanSvg').innerHTML = data.floorplan_svg;
      document.getElementById('sldSvg').innerHTML = data.sld_svg;

      if (boq && boq.items) {{
        const lang = document.getElementById('reportLang').value;
        const bRows = boq.items.map(it => {{
          const txt = lang === 'el'
            ? it.description_el
            : (lang === 'bilingual'
                ? `${{it.description_en}} / ${{it.description_el}}`
                : it.description_en);
          return `<tr>
            <td><code>${{escapeHtml(it.item_no)}}</code></td>
            <td>${{escapeHtml(it.category)}}</td>
            <td>${{escapeHtml(txt)}}</td>
            <td class="c">${{escapeHtml(it.unit)}}</td>
            <td class="r">${{it.quantity.toFixed(2)}}</td>
            <td class="r">€${{it.unit_rate_eur.toFixed(2)}}</td>
            <td class="r">€${{it.total_eur.toFixed(2)}}</td>
          </tr>`;
        }}).join('');
        const totals = `
          <tr><td colspan="6" class="r"><strong>Subtotal (excl. VAT)</strong></td>
            <td class="r"><strong>€${{boq.subtotal_eur.toFixed(2)}}</strong></td></tr>
          <tr><td colspan="6" class="r"><strong>Cyprus VAT (19%)</strong></td>
            <td class="r"><strong>€${{boq.vat_eur.toFixed(2)}}</strong></td></tr>
          <tr><td colspan="6" class="r"><strong>GRAND TOTAL (EUR incl. VAT)</strong></td>
            <td class="r"><strong>€${{boq.grand_total_eur.toFixed(2)}}</strong></td></tr>`;
        document.getElementById('boqBody').innerHTML = bRows + totals;
      }}

      const rank = {{ blocker: 0, warning: 1, info: 2 }};
      const sorted = [...d.review_flags].sort((a,b) => rank[a.severity] - rank[b.severity]);
      document.getElementById('flagsBody').innerHTML = sorted.map(f => {{
        const cls = f.severity === 'blocker'
          ? 'b-fail'
          : (f.severity === 'warning' ? 'b-warn' : 'b-info');
        const sev = escapeHtml(f.severity).toUpperCase();
        return `<tr>
          <td class="c"><span class="badge ${{cls}}">${{sev}}</span></td>
          <td><code>${{escapeHtml(f.code)}}</code></td>
          <td>${{escapeHtml(f.category)}}</td>
          <td class="c">${{escapeHtml(f.circuit_id || '-')}}</td>
          <td>${{escapeHtml(f.regulation_ref || '-')}}</td>
          <td>${{escapeHtml(f.message)}}</td>
        </tr>`;
      }}).join('');
    }}

    // Delegated listener for Pass-2 circuit table edits
    document.getElementById('circuitsBody').addEventListener('change', (ev) => {{
      const el = ev.target;
      if (el && el.dataset && el.dataset.cid && el.dataset.field) {{
        setCircuitOverride(el.dataset.cid, el.dataset.field, el.value);
      }}
    }});

    // Delegated click listener for Interactive Visual Floorplan (Room Reclass & DB1 Relocation)
    document.getElementById('floorplanSvg').addEventListener('click', (ev) => {{
      const svg = document.querySelector('#floorplanSvg svg');
      if (!svg) return;
      if (dbRelocateMode) {{
        const pt = svg.createSVGPoint();
        pt.x = ev.clientX;
        pt.y = ev.clientY;
        const ctm = svg.getScreenCTM();
        if (!ctm) return;
        const svgPt = pt.matrixTransform(ctm.inverse());
        const minx = parseFloat(svg.dataset.minx || '0');
        const maxy = parseFloat(svg.dataset.maxy || '0');
        const scale = parseFloat(svg.dataset.scale || '1');
        const padL = parseFloat(svg.dataset.padLeft || '48');
        const padT = parseFloat(svg.dataset.padTop || '56');
        const xMm = Math.round(minx + (svgPt.x - padL) / scale);
        const yMm = Math.round(maxy - (svgPt.y - padT) / scale);
        dbLocationOverride = {{ x_mm: xMm, y_mm: yMm }};
        toggleDbMode();
        runSolve();
        return;
      }}
      const poly = ev.target.closest('polygon[data-room-id]');
      if (!poly) return;
      selectedRoomId = poly.dataset.roomId;
      const curType = poly.dataset.roomType || 'storage';
      const lbl = poly.dataset.roomLabel || selectedRoomId;
      document.getElementById('roomEditLbl').textContent = `${{selectedRoomId}} (${{lbl}}):`;
      const sel = document.getElementById('roomTypeSel');
      sel.innerHTML = ROOM_TYPES.map(rt =>
        `<option value="${{rt}}" ${{rt === curType ? 'selected' : ''}}>${{rt}}</option>`
      ).join('');
      document.getElementById('roomEditBox').style.display = 'inline-flex';
    }});

    function authHeaders(isJson) {{
      const h = {{ 'X-Tenant-ID': document.getElementById('tenantId').value || 'anonymous' }};
      const key = (document.getElementById('apiKeyInput').value || '').trim();
      if (key) h['X-API-Key'] = key;
      if (isJson) h['Content-Type'] = 'application/json';
      return h;
    }}

    document.getElementById('apiKeyInput').addEventListener('change', (ev) => {{
      try {{ sessionStorage.setItem('cyelec_api_key', ev.target.value.trim()); }} catch (_) {{}}
    }});

    // DXF Layer Mapping Wizard trigger on file select
    document.getElementById('dxfFile').addEventListener('change', async (ev) => {{
      const f = ev.target.files[0];
      const box = document.getElementById('layerWizard');
      const rows = document.getElementById('layerRows');
      Object.keys(layerRoleChoices).forEach(k => delete layerRoleChoices[k]);
      if (!f || !f.name.toLowerCase().endsWith('.dxf')) {{
        box.style.display = 'none';
        return;
      }}
      const fd = new FormData();
      fd.append('file', f);
      try {{
        const resp = await fetch('/v1/dxf/layers', {{
          method: 'POST',
          headers: authHeaders(false),
          body: fd
        }});
        if (!resp.ok) return;
        const body = await resp.json();
        rows.innerHTML = body.layers.map(l => {{
          const lname = escapeHtml(l.layer_name);
          const opts = LAYER_ROLES.map(r => {{
            const sel = (r === 'auto') ? 'selected' : '';
            const lbl = (r === 'auto') ? `auto (${{l.inferred_role}})` : r;
            return `<option value="${{r}}" ${{sel}}>${{escapeHtml(lbl)}}</option>`;
          }}).join('');
          return `<div class="layer-row">
            <span title="${{lname}}">${{lname}} (${{l.entity_count}})</span>
            <select data-layer="${{lname}}">${{opts}}</select>
          </div>`;
        }}).join('');
        box.style.display = 'block';
      }} catch (_) {{
        box.style.display = 'none';
      }}
    }});

    document.getElementById('layerRows').addEventListener('change', (ev) => {{
      const el = ev.target;
      if (el && el.dataset && el.dataset.layer) {{
        layerRoleChoices[el.dataset.layer] = el.value;
      }}
    }});

    async function runSolve() {{
      const status = document.getElementById('statusLine');
      status.textContent = 'Running deterministic BS 7671 / EAC / NetworkX solve...';
      const fileInput = document.getElementById('dxfFile');
      const overrides = collectOverrides();

      try {{
        let resp;
        if (fileInput.files.length > 0) {{
          const fd = new FormData();
          fd.append('file', fileInput.files[0]);
          fd.append('overrides_json', JSON.stringify(overrides));
          const lm = collectLayerMapping();
          if (lm) fd.append('layer_mapping_json', JSON.stringify(lm));
          resp = await fetch('/v1/solve/dxf/preview', {{
            method: 'POST',
            headers: authHeaders(false),
            body: fd
          }});
        }} else {{
          const tmpl = document.getElementById('benchmarkSel').value;
          resp = await fetch(`/v1/solve/benchmark/${{tmpl}}`, {{
            method: 'POST',
            headers: authHeaders(true),
            body: JSON.stringify(overrides)
          }});
        }}
        if (!resp.ok) throw new Error(await resp.text());
        const data = await resp.json();
        renderResult(data);
        const ms = resp.headers.get('X-Process-Time-Ms') || '?';
        const sha = data.result.design.provenance.input_sha256.slice(0, 12);
        status.textContent = `Solved cleanly in ${{ms}} ms (SHA-256: ${{sha}}).`;
      }} catch (err) {{
        status.textContent = `Error: ${{err.message}}`;
      }}
    }}

    async function downloadBundle() {{
      const fileInput = document.getElementById('dxfFile');
      const overrides = collectOverrides();
      let resp;
      if (fileInput.files.length > 0) {{
        const fd = new FormData();
        fd.append('file', fileInput.files[0]);
        fd.append('overrides_json', JSON.stringify(overrides));
        const lm = collectLayerMapping();
        if (lm) fd.append('layer_mapping_json', JSON.stringify(lm));
        resp = await fetch('/v1/solve/dxf/bundle', {{
          method: 'POST',
          headers: authHeaders(false),
          body: fd
        }});
      }} else {{
        const tmpl = document.getElementById('benchmarkSel').value;
        resp = await fetch(`/v1/solve/benchmark/${{tmpl}}/bundle`, {{
          method: 'POST',
          headers: authHeaders(true),
          body: JSON.stringify(overrides)
        }});
      }}
      if (!resp.ok) {{ alert('Bundle failed: ' + await resp.text()); return; }}
      const blob = await resp.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = 'eac_submission_pack.zip';
      a.click();
      URL.revokeObjectURL(url);
    }}

    async function loadProjects() {{
      const resp = await fetch('/v1/projects', {{ headers: authHeaders(false) }});
      if (!resp.ok) return;
      const list = await resp.json();
      const sel = document.getElementById('projectSel');
      const cur = sel.value;
      sel.innerHTML = list.map(p =>
        `<option value="${{escapeHtml(p.project_id)}}">`
        + `${{escapeHtml(p.name)}} (${{p.revision_count}} rev)</option>`
      ).join('');
      if (cur && list.some(p => p.project_id === cur)) sel.value = cur;
      if (sel.value) loadRevisions();
    }}

    async function createAndSaveRevision() {{
      const st = document.getElementById('wsStatus');
      const sel = document.getElementById('projectSel');
      let pid = sel.value;
      const newName = document.getElementById('projName').value.trim();
      if (!pid || newName) {{
        const pr = await fetch('/v1/projects', {{
          method: 'POST',
          headers: authHeaders(true),
          body: JSON.stringify({{ name: newName || 'Cyprus Project' }})
        }});
        if (!pr.ok) {{ st.textContent = 'Project creation failed.'; return; }}
        const created = await pr.json();
        pid = created.project_id;
        document.getElementById('projName').value = '';
      }}
      const tmpl = document.getElementById('benchmarkSel').value;
      const resp = await fetch(`/v1/projects/${{encodeURIComponent(pid)}}/revisions`, {{
        method: 'POST',
        headers: authHeaders(true),
        body: JSON.stringify({{
          benchmark_template: tmpl,
          overrides: collectOverrides(),
          carry_forward_overrides: true
        }})
      }});
      if (!resp.ok) {{ st.textContent = 'Save revision failed.'; return; }}
      const rec = await resp.json();
      st.textContent = `Saved Rev ${{rec.summary.revision_number}} cleanly.`;
      await loadProjects();
      sel.value = pid;
      await loadRevisions();
    }}

    async function loadRevisions() {{
      const pid = document.getElementById('projectSel').value;
      if (!pid) return;
      const resp = await fetch(`/v1/projects/${{encodeURIComponent(pid)}}/revisions`, {{
        headers: authHeaders(false)
      }});
      if (!resp.ok) return;
      const revs = await resp.json();
      const rows = revs.map(r => `<tr>
        <td><strong>Rev ${{r.revision_number}}</strong></td>
        <td><code>${{escapeHtml(r.input_sha256.slice(0, 12))}}</code></td>
        <td>${{escapeHtml(r.supply_tier)}}</td>
        <td class="r">${{r.diversified_kva.toFixed(2)}} kVA</td>
        <td class="r">€${{r.boq_grand_total_eur.toFixed(2)}}</td>
        <td>${{escapeHtml(r.created_at)}}</td>
      </tr>`).join('');
      document.getElementById('revListBox').innerHTML = `<table>
        <thead><tr>
          <th>Rev</th><th>Input SHA</th><th>Supply Tier</th>
          <th class="r">Div. kVA</th><th class="r">BOQ (EUR)</th><th>Timestamp</th>
        </tr></thead><tbody>${{rows}}</tbody></table>`;
    }}

    async function diffLatestRevisions() {{
      const pid = document.getElementById('projectSel').value;
      const box = document.getElementById('revDiffBox');
      if (!pid) return;
      const rResp = await fetch(`/v1/projects/${{encodeURIComponent(pid)}}/revisions`, {{
        headers: authHeaders(false)
      }});
      const revs = await rResp.json();
      if (revs.length < 2) {{
        box.textContent = 'Save at least 2 revisions to compare diffs.';
        return;
      }}
      const a = revs[revs.length - 2].revision_number;
      const b = revs[revs.length - 1].revision_number;
      const dResp = await fetch(
        `/v1/projects/${{encodeURIComponent(pid)}}/diff/${{a}}/${{b}}`,
        {{ headers: authHeaders(false) }}
      );
      if (!dResp.ok) return;
      const diff = await dResp.json();
      const cRows = diff.circuit_deltas.map(c => `<tr>
        <td><code>${{escapeHtml(c.circuit_id)}}</code></td>
        <td>${{escapeHtml(c.change)}}</td>
        <td>${{escapeHtml(c.old_summary || '-')}}</td>
        <td>${{escapeHtml(c.new_summary || '-')}}</td>
      </tr>`).join('');
      box.innerHTML = `<p><strong>Diff Rev ${{a}} -&gt; Rev ${{b}}:</strong>
        ΔkVA=${{diff.diversified_kva_delta}} | ΔConduit=${{diff.conduit_m_delta}} m |
        ΔBOQ=€${{diff.boq_eur_delta.toFixed(2)}}</p>
        <table><thead><tr>
          <th>Circuit</th><th>Change</th><th>Rev ${{a}}</th><th>Rev ${{b}}</th>
        </tr></thead><tbody>${{cRows || '<tr><td colspan="4">No circuit deltas.</td></tr>'}}
        </tbody></table>`;
    }}

    window.addEventListener('DOMContentLoaded', () => {{
      try {{
        const savedKey = sessionStorage.getItem('cyelec_api_key');
        if (savedKey) document.getElementById('apiKeyInput').value = savedKey;
      }} catch (_) {{}}
      runSolve();
    }});
  </script>
</body>
</html>
"""
