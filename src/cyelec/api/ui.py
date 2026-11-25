"""Zero-dependency, GDPR-safe Interactive ETEK Engineer Review & Pass-2 Override Workbench UI."""

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
      grid-template-columns: 310px 1fr;
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
      font-size: 13px;
      color: var(--navy);
      text-transform: uppercase;
      letter-spacing: 0.4px;
      border-bottom: 1px solid var(--border);
      padding-bottom: 6px;
    }}
    label {{
      display: block;
      font-weight: 600;
      margin: 9px 0 3px;
      font-size: 11.5px;
      color: var(--muted);
    }}
    input, select {{
      width: 100%;
      padding: 7px 8px;
      border: 1px solid var(--border);
      border-radius: 4px;
      font-size: 12.5px;
      background: #fff;
    }}
    .btn {{
      display: inline-block;
      width: 100%;
      padding: 9px 12px;
      border: none;
      border-radius: 4px;
      font-weight: 700;
      cursor: pointer;
      margin-top: 10px;
      font-size: 12.5px;
    }}
    .btn-primary {{ background: var(--navy); color: #fff; }}
    .btn-primary:hover {{ background: var(--navy-light); }}
    .btn-secondary {{ background: #e2e8f0; color: var(--navy); }}
    .btn-secondary:hover {{ background: #cbd5e0; }}
    .kpi-grid {{
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      gap: 12px;
      margin-bottom: 14px;
    }}
    .kpi {{
      background: var(--card);
      border: 1px solid var(--border);
      border-left: 4px solid var(--navy);
      border-radius: 6px;
      padding: 10px 12px;
    }}
    .kpi .k {{ font-size: 10.5px; color: var(--muted); text-transform: uppercase; }}
    .kpi .v {{ font-size: 15px; font-weight: 700; color: var(--navy); margin-top: 3px; }}
    .tabs {{
      display: flex;
      gap: 6px;
      margin-bottom: 12px;
      border-bottom: 2px solid var(--border);
    }}
    .tab {{
      padding: 8px 14px;
      cursor: pointer;
      font-weight: 600;
      color: var(--muted);
      border-bottom: 2px solid transparent;
      margin-bottom: -2px;
      background: none;
      border-top: none;
      border-left: none;
      border-right: none;
      font-size: 12.5px;
    }}
    .tab.active {{ color: var(--navy); border-bottom-color: var(--navy); }}
    .tab-pane {{ display: none; }}
    .tab-pane.active {{ display: block; }}
    .svg-box {{
      background: #fff;
      border: 1px solid var(--border);
      border-radius: 4px;
      padding: 8px;
      overflow: auto;
      max-height: 70vh;
    }}
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
  </style>
</head>
<body>
  <header>
    <h1>cyelec-pipeline — ETEK Engineer Review &amp; Pass-2 Reconciliation Workbench</h1>
    <div class="meta">v{__version__} | BS 7671 / IEC 60364 / Cyprus EAC | Stateless EU SaaS</div>
  </header>

  <div class="layout">
    <aside class="panel">
      <h2>1. Input &amp; Tenant</h2>
      <label for="tenantId">Tenant ID (X-Tenant-ID)</label>
      <input id="tenantId" value="cy-etek-demo" />

      <label for="benchmarkSel">Cyprus Benchmark Fixture</label>
      <select id="benchmarkSel">
        <option value="apartment_3bed">3-Bed Cyprus Apartment (120 m²)</option>
        <option value="villa_pv_ev">Detached Villa + 7.5 kVA PV + 7.4 kW EV (216 m²)</option>
        <option value="commercial_office">
          Commercial Office 450 m² (CT Metering &amp; Substation)
        </option>
      </select>

      <label for="dxfFile">Or Upload Architectural DXF (.dxf)</label>
      <input id="dxfFile" type="file" accept=".dxf,.ifc" />

      <h2 style="margin-top:16px;">2. Pass-2 Design Overrides</h2>
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
          <div class="k">ETEK Review Gate</div><div class="v" id="kFlags">-</div>
        </div>
      </div>

      <div class="panel">
        <div class="tabs">
          <button class="tab active" onclick="openTab('tabCircuits', this)">
            Circuits &amp; Pass-2 Overrides
          </button>
          <button class="tab" onclick="openTab('tabFloorplan', this)">
            Electrical Floorplan (SVG)
          </button>
          <button class="tab" onclick="openTab('tabSld', this)">
            Single-Line Diagram (SVG)
          </button>
          <button class="tab" onclick="openTab('tabFlags', this)">ETEK Review Flags</button>
        </div>

        <div id="tabCircuits" class="tab-pane active">
          <div id="balanceBars" style="margin-bottom:14px;"></div>
          <div style="overflow-x:auto;">
            <table>
              <thead>
                <tr>
                  <th>ID</th><th>Description</th><th>Phase (Override)</th><th>Cg</th>
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
          <div id="floorplanSvg" class="svg-box">Run a solve to render floorplan overlay.</div>
        </div>

        <div id="tabSld" class="tab-pane">
          <div id="sldSvg" class="svg-box">Run a solve to render Single-Line Diagram.</div>
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
      </div>
    </main>
  </div>

  <script>
    const circuitOverrides = {{}};

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

    function collectOverrides() {{
      const ov = {{
        eac_profile: document.getElementById('eacProfile').value,
        earthing_system: document.getElementById('earthing').value,
        circuit_overrides: circuitOverrides
      }};
      const ze = document.getElementById('zeRa').value;
      if (ze) ov.ze_or_ra_ohm = parseFloat(ze);
      const amb = document.getElementById('ambientC').value;
      if (amb) ov.ambient_temp_c = parseInt(amb, 10);
      const pv = document.getElementById('pvKva').value;
      if (pv) ov.pv_capacity_kva = parseFloat(pv);
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

      document.getElementById('kSupply').textContent = eac.recommended_supply_tier;
      const divKva = eac.total_diversified_kva.toFixed(2);
      const conKva = eac.total_connected_kva.toFixed(2);
      document.getElementById('kKva').textContent = `${{divKva}} / ${{conKva}} kVA`;
      document.getElementById('kBal').textContent = pb
        ? `${{pb.imbalance_pct.toFixed(2)}}% | ${{sp.total_conduit_m.toFixed(1)}} m`
        : `1P | ${{sp.total_conduit_m.toFixed(1)}} m`;

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

      document.getElementById('circuitsBody').innerHTML = d.circuits.map(c => {{
        const s = c.spec;
        const cid = escapeHtml(s.circuit_id);
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

    document.getElementById('circuitsBody').addEventListener('change', (ev) => {{
      const el = ev.target;
      if (el && el.dataset && el.dataset.cid && el.dataset.field) {{
        setCircuitOverride(el.dataset.cid, el.dataset.field, el.value);
      }}
    }});

    async function runSolve() {{
      const status = document.getElementById('statusLine');
      status.textContent = 'Running deterministic BS 7671 / EAC / NetworkX solve...';
      const tenant = document.getElementById('tenantId').value || 'anonymous';
      const fileInput = document.getElementById('dxfFile');
      const overrides = collectOverrides();

      try {{
        let resp;
        if (fileInput.files.length > 0) {{
          const fd = new FormData();
          fd.append('file', fileInput.files[0]);
          fd.append('overrides_json', JSON.stringify(overrides));
          resp = await fetch('/v1/solve/dxf/preview', {{
            method: 'POST',
            headers: {{ 'X-Tenant-ID': tenant }},
            body: fd
          }});
        }} else {{
          const tmpl = document.getElementById('benchmarkSel').value;
          resp = await fetch(`/v1/solve/benchmark/${{tmpl}}`, {{
            method: 'POST',
            headers: {{ 'Content-Type': 'application/json', 'X-Tenant-ID': tenant }},
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
      const tenant = document.getElementById('tenantId').value || 'anonymous';
      const fileInput = document.getElementById('dxfFile');
      const overrides = collectOverrides();
      let resp;
      if (fileInput.files.length > 0) {{
        const fd = new FormData();
        fd.append('file', fileInput.files[0]);
        fd.append('overrides_json', JSON.stringify(overrides));
        resp = await fetch('/v1/solve/dxf/bundle', {{
          method: 'POST',
          headers: {{ 'X-Tenant-ID': tenant }},
          body: fd
        }});
      }} else {{
        const tmpl = document.getElementById('benchmarkSel').value;
        resp = await fetch(`/v1/solve/benchmark/${{tmpl}}/bundle`, {{
          method: 'POST',
          headers: {{ 'Content-Type': 'application/json', 'X-Tenant-ID': tenant }},
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

    window.addEventListener('DOMContentLoaded', runSolve);
  </script>
</body>
</html>
"""
