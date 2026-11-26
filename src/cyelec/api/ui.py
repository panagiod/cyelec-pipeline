"""Zero-dependency, GDPR-safe Interactive ETEK Engineer Review & Visual Pass-2 Workbench UI."""

from __future__ import annotations

import html

from cyelec import __version__
from cyelec.api.docs_ui import render_workbench_guide_tab_html


def render_etek_workbench_html(
    base_url: str = "",
    *,
    show_api_docs: bool = True,
    ifc_available: bool = False,
    dwg_available: bool = False,
    auth_required: bool = False,
    invite_code_required: bool = False,
) -> str:
    """Return self-contained HTML/CSS/JS ETEK Engineer Review Dashboard served at GET /."""
    prefix = base_url.rstrip("/")
    og_img = html.escape(f"{prefix}/og-thumbnail.jpg", quote=True)
    logo_url = html.escape(f"{prefix}/logo.png", quote=True)
    guide_tab_html = render_workbench_guide_tab_html(show_api_docs=show_api_docs)
    api_docs_link = (
        '<a class="hdr-link" href="/docs" target="_blank" rel="noopener">API Docs</a>'
        if show_api_docs
        else ""
    )
    exts = [".dxf"]
    if dwg_available:
        exts.append(".dwg")
    if ifc_available:
        exts.append(".ifc")
    upload_accept = ",".join(exts)
    fmt_list = " / ".join(exts)
    mf_exts = ".dxf/.dwg" if dwg_available else ".dxf"
    upload_label = (
        f"Or Upload Architectural Floorplan(s) ({fmt_list} — select 2+ {mf_exts} for multi-floor)"
    )
    auth_req_js = "true" if auth_required else "false"
    invite_req_js = "true" if invite_code_required else "false"
    invite_disp = "block" if invite_code_required else "none"
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <meta name="theme-color" content="#1b365d" />
  <meta
    name="description"
    content="Deterministic BS 7671, IEC 60364 &amp; Cyprus EAC electrical engineering workbench."
  />
  <meta property="og:type" content="website" />
  <meta property="og:title" content="CyElec — ETEK Electrical Engineering &amp; EAC Workbench" />
  <meta
    property="og:description"
    content="Deterministic BS 7671 / IEC 60364 / Cyprus EAC DXF layout, SLD, BOQ &amp; balancing."
  />
  <meta property="og:image" content="{og_img}" />
  <meta property="og:image:width" content="1200" />
  <meta property="og:image:height" content="630" />
  <meta name="twitter:card" content="summary_large_image" />
  <meta name="twitter:title" content="CyElec — ETEK Electrical Engineering &amp; EAC Workbench" />
  <meta name="twitter:image" content="{og_img}" />
  <link rel="icon" type="image/svg+xml" href="/favicon.svg" />
  <link rel="icon" type="image/x-icon" href="/favicon.ico" />
  <link rel="apple-touch-icon" href="{logo_url}" />
  <title>CyElec — ETEK Engineer Review &amp; Pass-2 Workbench</title>
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
      padding: 10px 20px;
      display: flex;
      justify-content: space-between;
      align-items: center;
      border-bottom: 3px solid #0f2342;
    }}
    .brand-lockup {{
      display: flex;
      align-items: center;
      gap: 10px;
    }}
    .brand-logo {{
      width: 34px;
      height: 34px;
      border-radius: 7px;
      border: 1px solid rgba(56, 189, 248, 0.45);
      display: block;
    }}
    .hdr-right {{
      display: flex;
      align-items: center;
      gap: 12px;
    }}
    .hdr-link {{
      color: #ffffff;
      text-decoration: none;
      font-size: 11.5px;
      font-weight: 600;
      padding: 5px 10px;
      border-radius: 5px;
      border: 1px solid rgba(56, 189, 248, 0.5);
      background: rgba(2, 132, 199, 0.35);
    }}
    .hdr-link:hover {{ background: rgba(2, 132, 199, 0.6); }}
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
    .auth-backdrop {{
      position: fixed;
      inset: 0;
      background: rgba(16, 42, 67, 0.72);
      display: none;
      align-items: center;
      justify-content: center;
      z-index: 9999;
      padding: 16px;
    }}
    .auth-backdrop.open {{ display: flex; }}
    .auth-card {{
      background: #ffffff;
      border-radius: 10px;
      border: 1px solid var(--border);
      max-width: 430px;
      width: 100%;
      padding: 20px 22px;
      box-shadow: 0 18px 42px rgba(16, 42, 67, 0.28);
    }}
    .auth-card h2 {{ margin: 0 0 6px 0; font-size: 16px; color: var(--navy); border: none; }}
    .auth-tabs {{ display: flex; gap: 6px; margin: 12px 0; }}
    .auth-tab {{
      flex: 1;
      padding: 7px 10px;
      border: 1px solid var(--border);
      border-radius: 5px;
      background: #f8fafc;
      color: var(--navy);
      font-weight: 600;
      font-size: 12px;
      cursor: pointer;
    }}
    .auth-tab.active {{ background: var(--navy); color: #fff; border-color: var(--navy); }}
    .acct-pill {{
      background: rgba(255, 255, 255, 0.14);
      border: 1px solid rgba(255, 255, 255, 0.28);
      border-radius: 999px;
      padding: 4px 11px;
      font-size: 11.5px;
      color: #ffffff;
      cursor: pointer;
    }}
  </style>
</head>
<body>
  <div id="authModal" class="auth-backdrop">
    <div class="auth-card">
      <div style="display:flex;align-items:center;gap:10px;margin-bottom:4px;">
        <img src="/logo.png" alt="CyElec" width="32" height="32" style="border-radius:6px;" />
        <div>
          <h2>CyElec — ETEK Engineer Portal</h2>
          <div style="font-size:11.5px;color:var(--muted);">
            Sign in or register your Cyprus electrical engineering firm
          </div>
        </div>
      </div>
      <div class="auth-tabs">
        <button id="mTabLogin" class="auth-tab active" onclick="switchAuthModalTab('login')">
          Sign In
        </button>
        <button id="mTabReg" class="auth-tab" onclick="switchAuthModalTab('register')">
          Create Firm Account
        </button>
      </div>
      <div id="modalRegFields" style="display:none;">
        <label for="mFirm">Consulting Firm Name</label>
        <input id="mFirm" placeholder="e.g., Kypros MEP Consulting Ltd" />
        <label for="mName">Lead Engineer Full Name</label>
        <input id="mName" placeholder="e.g., N. Papadopoulos" />
        <div id="mInviteWrap" style="display:{invite_disp};">
          <label for="mInvite">Closed Pilot Invite Code</label>
          <input id="mInvite" placeholder="Enter your pilot invite code" />
        </div>
      </div>
      <label for="mEmail">Work Email</label>
      <input id="mEmail" type="email" placeholder="engineer@yourfirm.cy" />
      <label for="mPass">Password</label>
      <input id="mPass" type="password" placeholder="Min 8 characters" />
      <div id="mError" style="color:#c53030;font-size:11.5px;margin-top:8px;"></div>
      <div style="display:flex;gap:8px;margin-top:12px;">
        <button id="mSubmitBtn" class="btn btn-primary" style="flex:1;margin:0;"
          onclick="submitAuthModal()">Sign In</button>
        <button id="mCancelBtn" class="btn btn-secondary" style="width:auto;margin:0;"
          onclick="closeAuthModal()">Close</button>
      </div>
      <p style="font-size:11px;color:var(--muted);margin:10px 0 0 0;">
        Forgot your password? Contact your firm Owner (Team Seats -> Reset Password) or Pilot
        Administrator for instant recovery.
      </p>
    </div>
  </div>

  <header>
    <div class="brand-lockup">
      <img src="/logo.png" alt="CyElec" class="brand-logo" width="34" height="34" />
      <h1>CyElec — ETEK Engineer Review &amp; Visual Pass-2 Workbench</h1>
    </div>
    <div class="hdr-right">
      <button id="hdrAccountBtn" class="acct-pill" onclick="openAuthModal('login')">
        Sign In / Register
      </button>
      <a class="hdr-link" href="/guide" target="_blank" rel="noopener">
        User Guide &amp; Know-How
      </a>
      {api_docs_link}
      <div class="meta">v{__version__} | BS 7671 / Cyprus EAC</div>
    </div>
  </header>

  <div class="layout">
    <aside class="panel">
      <h2>1. Input &amp; CAD Layer Wizard</h2>
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

      <label for="dxfFile">{upload_label}</label>
      <input id="dxfFile" type="file" accept="{upload_accept}" multiple />
      <div id="multiFloorBox" class="layer-box">
        <strong style="font-size:11px;color:#1b365d;">
          Multi-Floor Combiner (MDB -&gt; Sub-DBs)
        </strong>
        <div id="multiFloorRows"></div>
      </div>
      <div id="layerWizard" class="layer-box">
        <strong style="font-size:11px;color:#1b365d;">CAD Layer Mapping Wizard</strong>
        <div id="layerRows"></div>
      </div>

      <details style="margin:10px 0 4px 0;padding:6px 8px;background:#f8fafc;
                      border:1px solid var(--border);border-radius:4px;">
        <summary style="font-size:11px;color:var(--muted);cursor:pointer;font-weight:600;">
          Advanced / API Key Access (Optional)
        </summary>
        <label for="tenantId">Tenant Workspace ID</label>
        <input id="tenantId" value="cy-etek-demo" />
        <label for="apiKeyInput">Tenant API Key (session only)</label>
        <input id="apiKeyInput" type="password" placeholder="Optional: cyelec_live_..." />
      </details>

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

      <label style="display:flex;align-items:center;gap:6px;margin-top:8px;cursor:pointer;">
        <input id="corridorCgChk" type="checkbox" style="width:auto;" />
        <span>Apply Auto Corridor Cg Grouping (Trunking / Tray Mode)</span>
      </label>
      <label style="display:flex;align-items:center;gap:6px;margin-top:6px;cursor:pointer;">
        <input id="ttMainRcdChk" type="checkbox" checked style="width:auto;" />
        <span>Include 100mA Type-S Main RCD on TT SLD (E-201)</span>
      </label>

      <h2 style="margin-top:14px;">3. Branding, BOQ &amp; Language</h2>
      <label for="reportLang">Deliverable Language</label>
      <select id="reportLang">
        <option value="en">English (EN)</option>
        <option value="el">Ελληνικά (EL)</option>
        <option value="bilingual">Bilingual (EN / EL)</option>
      </select>
      <label for="vatRateSel">Cyprus VAT Rate (BOQ)</label>
      <select id="vatRateSel">
        <option value="0.19">19% Standard Cyprus VAT</option>
        <option value="0.05">5% Reduced Primary Residence VAT</option>
      </select>
      <label style="display:flex;align-items:center;gap:6px;margin-top:8px;cursor:pointer;">
        <input id="tenderBoqChk" type="checkbox" style="width:auto;" />
        <span>Unpriced Tender BOQ (Blank Contractor Rates)</span>
      </label>
      <label for="firmName">Consulting Firm Name</label>
      <input id="firmName" placeholder="e.g., Kypros MEP Consulting Ltd" />
      <label for="engName">ETEK Engineer Name</label>
      <input id="engName" placeholder="e.g., N. Papadopoulos" />
      <label for="etekReg">ETEK Registration No.</label>
      <input id="etekReg" placeholder="e.g., ETEK-E-4821" />
      <button class="btn btn-secondary" onclick="saveFirmDefaults()">
        Save Firm Branding &amp; VAT Defaults
      </button>

      <details id="briefBox" style="margin-top:10px;border:1px solid #93c5fd;border-radius:6px;
               padding:8px 10px;background:#eff6ff;">
        <summary style="font-size:11.5px;font-weight:700;color:#1e3a8a;cursor:pointer;">
          3b. 60-Sec Cyprus Engineering Brief (HVAC / ELV / Site / KNX)
        </summary>
        <label for="briefHvac">HVAC Plant System</label>
        <select id="briefHvac">
          <option value="split_ac">Split A/C Units (20A DP Isolators)</option>
          <option value="heat_pump_ufh">Air-to-Water Heat Pump (3P) + UFH Manifolds</option>
          <option value="vrv">VRV / VRF Central (3P) + Indoor FCUs</option>
        </select>
        <div class="row2">
          <div>
            <label for="briefEvKw">EV Wallbox (Sec. 722)</label>
            <select id="briefEvKw">
              <option value="">Auto / Model</option>
              <option value="0">None (0 kW)</option>
              <option value="7.4">7.4 kW (1P 32A)</option>
              <option value="11">11 kW (3P 16A)</option>
              <option value="22">22 kW (3P 32A)</option>
            </select>
          </div>
          <div>
            <label for="briefConduitStyle">CAD Conduit Style</label>
            <select id="briefConduitStyle">
              <option value="homerun_arcs">Cyprus Home-Run Arrows</option>
              <option value="full_routed">Full Routed Conduits</option>
            </select>
          </div>
        </div>
        <div class="row2">
          <div>
            <label for="briefEarthing">Site Earthing</label>
            <select id="briefEarthing">
              <option value="foundation_ring">Foundation / Ring Earth</option>
              <option value="rod_pit">Copper Rod Pit (Ø16)</option>
            </select>
          </div>
          <div>
            <label for="briefHeightM">Building Height (m)</label>
            <input id="briefHeightM" type="number" step="0.5" min="3" max="40" value="7.5" />
          </div>
        </div>
        <div class="row2">
          <div>
            <label for="briefPlotW">Plot Width (m)</label>
            <input id="briefPlotW" type="number" step="1" min="6" max="150" value="18" />
          </div>
          <div>
            <label for="briefPlotL">Plot Length (m)</label>
            <input id="briefPlotL" type="number" step="1" min="6" max="150" value="24" />
          </div>
        </div>
        <label style="display:flex;align-items:center;gap:6px;margin-top:6px;cursor:pointer;">
          <input id="briefShutters" type="checkbox" checked style="width:auto;" />
          <span>Motorized Electric Shutters</span>
        </label>
        <label style="display:flex;align-items:center;gap:6px;margin-top:4px;cursor:pointer;">
          <input id="briefElv" type="checkbox" checked style="width:auto;" />
          <span>ELV Pack (9U Rack, Cat6, TV, Wi-Fi, Intercom, Alarm, CCTV)</span>
        </label>
        <label style="display:flex;align-items:center;gap:6px;margin-top:4px;cursor:pointer;">
          <input id="briefSmoke" type="checkbox" checked style="width:auto;" />
          <span>BS 5839-6 Grade D1 Interlinked Smoke/Heat</span>
        </label>
        <label style="display:flex;align-items:center;gap:6px;margin-top:4px;cursor:pointer;">
          <input id="briefKitchenFcus" type="checkbox" checked style="width:auto;" />
          <span>Kitchen Hood / Fridge / Microwave FCUs</span>
        </label>
        <label style="display:flex;align-items:center;gap:6px;margin-top:4px;cursor:pointer;">
          <input id="briefPumps" type="checkbox" checked style="width:auto;" />
          <span>Water Booster &amp; Hot Water Recirc Pumps</span>
        </label>
        <label style="display:flex;align-items:center;gap:6px;margin-top:4px;cursor:pointer;">
          <input id="briefGate" type="checkbox" checked style="width:auto;" />
          <span>Sliding Yard Gate / Garage Motor</span>
        </label>
        <label style="display:flex;align-items:center;gap:6px;margin-top:4px;cursor:pointer;">
          <input id="briefSite" type="checkbox" checked style="width:auto;" />
          <span>Site Trenching, Garden Bollards &amp; IEC 62305-2 LPS</span>
        </label>
        <label style="display:flex;align-items:center;gap:6px;margin-top:4px;cursor:pointer;">
          <input id="briefPool" type="checkbox" style="width:auto;" />
          <span>Swimming Pool Plant Sub-DB (BS 7671 Sec. 702)</span>
        </label>
        <label style="display:flex;align-items:center;gap:6px;margin-top:4px;cursor:pointer;">
          <input id="briefKnx" type="checkbox" style="width:auto;" />
          <span>Smart Home KNX Bus Automation &amp; Keypads</span>
        </label>
      </details>

      <details id="eacMetaBox" style="margin-top:10px;border:1px solid #cbd5e0;border-radius:6px;
               padding:8px 10px;background:#f8fafc;">
        <summary style="font-size:11.5px;font-weight:700;color:#1b365d;cursor:pointer;">
          4. EAC Cadastral &amp; Contractor Metadata (Ε-ΔΔ-516)
        </summary>
        <label for="eacDistrict">EAC District (Επαρχία)</label>
        <select id="eacDistrict">
          <option value="">-- Optional --</option>
          <option value="Nicosia">Nicosia (Λευκωσία)</option>
          <option value="Limassol">Limassol (Λεμεσός)</option>
          <option value="Larnaca">Larnaca (Λάρνακα)</option>
          <option value="Paphos">Paphos (Πάφος)</option>
          <option value="Famagusta">Famagusta (Αμμόχωστος)</option>
        </select>
        <label for="eacMunicipality">Municipality / Village</label>
        <input id="eacMunicipality" placeholder="e.g., Strovolos / Germasogeia" />
        <label for="eacSheetPlan">Cadastral Sheet / Plan</label>
        <input id="eacSheetPlan" placeholder="e.g., 21/54E2" />
        <label for="eacPlotNo">Plot No. (Αρ. Τεμαχίου)</label>
        <input id="eacPlotNo" placeholder="e.g., 412" />
        <label for="eacPermitNo">Building Permit No.</label>
        <input id="eacPermitNo" placeholder="e.g., BP-2026/0841" />
        <label for="eacOwner">Property Owner / Applicant</label>
        <input id="eacOwner" placeholder="Owner full name or company" />
        <label for="eacTariff">EAC Tariff Code (optional)</label>
        <input id="eacTariff" placeholder="Auto (e.g., Code 01 Domestic)" />
        <label for="eacContractor">Licensed Installer Name</label>
        <input id="eacContractor" placeholder="Class A Electrical Contractor" />
        <label for="eacHmyReg">Contractor HMY Licence No.</label>
        <input id="eacHmyReg" placeholder="e.g., HMY-A-1420" />
      </details>

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
            Drawing Set (E-100..E-103)
          </button>
          <button class="tab" onclick="openTab('tabSite', this)">
            Site &amp; Earthing (E-104)
          </button>
          <button class="tab" onclick="openTab('tabSld', this)">
            SLD (E-201)
          </button>
          <button class="tab" onclick="openTab('tabRiser', this)">
            Riser &amp; DIN-Rail DB (E-202)
          </button>
          <button class="tab" onclick="openTab('tabDetails', this)">
            Details &amp; Notes (E-301)
          </button>
          <button class="tab" onclick="openTab('tabBoq', this)">
            Bill of Quantities (EUR)
          </button>
          <button class="tab" onclick="openTab('tabFlags', this)">ETEK Review Flags</button>
          <button class="tab" onclick="openTab('tabWorkspace', this); loadProjects();">
            Workspace &amp; Revision Diff
          </button>
          <button class="tab" onclick="openTab('tabGuide', this)">
            Guide &amp; Know-How
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
          <div class="fp-toolbar" style="margin-bottom:8px;gap:6px;flex-wrap:wrap;">
            <strong>Sheet View:</strong>
            <button id="btnSheetCombined" class="btn btn-primary btn-sm"
              onclick="selectDrawingSheet('combined')">E-100 Combined</button>
            <button id="btnSheetLighting" class="btn btn-secondary btn-sm"
              onclick="selectDrawingSheet('lighting')">E-101 Lighting</button>
            <button id="btnSheetPower" class="btn btn-secondary btn-sm"
              onclick="selectDrawingSheet('power')">E-102 Power &amp; Plant</button>
            <button id="btnSheetElv" class="btn btn-secondary btn-sm"
              onclick="selectDrawingSheet('elv')">E-103 ELV &amp; Security</button>
            <span style="margin-left:6px;">|</span>
            <select id="anchorTargetSel" style="width:138px;" title="Select key anchor to relocate"
              onchange="updateAnchorBtnLabel()">
              <option value="db">Anchor: DB1 / MDB</option>
              <option value="meter">Anchor: EAC Meter</option>
              <option value="earth_pit">Anchor: TT Earth Pit</option>
              <option value="telecom">Anchor: 9U Telecom</option>
            </select>
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

        <div id="tabSite" class="tab-pane">
          <div id="siteSvg" class="svg-box">
            Run a solve to render E-104 Site Works, Underground Trenching, Earthing &amp; LPS.
          </div>
        </div>

        <div id="tabSld" class="tab-pane">
          <div id="sldSvg" class="svg-box">Run a solve to render Single-Line Diagram.</div>
        </div>

        <div id="tabRiser" class="tab-pane">
          <div id="riserSvg" class="svg-box">
            Run a solve to render E-202 Vertical Riser Diagram &amp; 18mm DIN-Rail DB Elevation.
          </div>
        </div>

        <div id="tabDetails" class="tab-pane">
          <div id="detailsSvg" class="svg-box">
            Run a solve to render E-301 Construction Details, Earthing &amp; General Notes.
          </div>
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
            <button class="btn btn-secondary btn-sm" onclick="exportTenantGdpr()">
              Export Data (GDPR Art. 20)
            </button>
            <button class="btn btn-active btn-sm" onclick="eraseTenantGdpr()">
              Erase Tenant (GDPR Art. 17)
            </button>
            <span id="wsStatus" style="color:var(--muted);"></span>
          </div>
          <div id="revListBox" style="margin-bottom:12px;"></div>
          <div id="revDiffBox" style="margin-bottom:14px;"></div>

          <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));
                      gap:12px;border-top:1px solid var(--border);padding-top:12px;">
            <div class="panel" style="margin:0;">
              <h2>Firm Sign In / Self-Serve Registration (JWT)</h2>
              <div id="authBadge" style="font-size:11.5px;color:var(--muted);margin-bottom:6px;">
                Not signed in via JWT.
              </div>
              <input id="authFirm" placeholder="Firm Name (for new signup)"
                style="margin-bottom:5px;" />
              <input id="authDisplayName" placeholder="Lead Engineer Name (for new signup)"
                style="margin-bottom:5px;" />
              <input id="authInvite" placeholder="Closed Pilot Invite Code (if required)"
                style="margin-bottom:5px;display:{invite_disp};" />
              <input id="authEmail" type="email" placeholder="Work Email"
                style="margin-bottom:5px;" />
              <input id="authPass" type="password" placeholder="Password (min 8 chars)"
                style="margin-bottom:8px;" />
              <div class="fp-toolbar" style="margin:0;">
                <button class="btn btn-primary btn-sm" onclick="firmLogin()">Sign In</button>
                <button class="btn btn-secondary btn-sm" onclick="firmRegister()">
                  Register Firm
                </button>
                <button class="btn btn-secondary btn-sm" onclick="firmLogout()">Sign Out</button>
              </div>
              <details style="margin-top:8px;font-size:11.5px;">
                <summary style="cursor:pointer;color:var(--navy);font-weight:600;">
                  Change My Password
                </summary>
                <input id="chgCurPass" type="password" placeholder="Current Password"
                  style="margin:6px 0 4px 0;" />
                <input id="chgNewPass" type="password" placeholder="New Password (8+ chars)"
                  style="margin-bottom:6px;" />
                <button class="btn btn-secondary btn-sm" onclick="changeMyPassword()">
                  Save New Password
                </button>
              </details>
            </div>

            <div class="panel" style="margin:0;">
              <h2>Plan, Monthly Solve Quota &amp; Stripe Billing</h2>
              <div id="subBox" style="font-size:12px;margin-bottom:8px;color:var(--muted);">
                Click Refresh Billing to inspect plan &amp; monthly solves.
              </div>
              <div class="fp-toolbar" style="margin:0;">
                <button class="btn btn-secondary btn-sm" onclick="loadSubscriptionAndTeam()">
                  Refresh Billing &amp; Team
                </button>
                <button class="btn btn-primary btn-sm" onclick="startCheckout('pilot')">
                  Pilot €29/mo
                </button>
                <button class="btn btn-primary btn-sm" onclick="startCheckout('pro')">
                  Pro €99/mo
                </button>
                <button class="btn btn-secondary btn-sm" onclick="openBillingPortal()">
                  Billing Portal
                </button>
              </div>
            </div>

            <div class="panel" style="margin:0;">
              <h2>Firm Team Seats (Owner / Engineer / Viewer)</h2>
              <div id="teamBox" style="font-size:11.5px;margin-bottom:8px;color:var(--muted);">
                No firm seats loaded.
              </div>
              <input id="memEmail" type="email" placeholder="Member Email"
                style="margin-bottom:4px;" />
              <input id="memName" placeholder="Full Name" style="margin-bottom:4px;" />
              <div class="row2" style="margin-bottom:6px;">
                <input id="memPass" type="password" placeholder="Temp Password (8+)" />
                <select id="memRole">
                  <option value="engineer">Engineer</option>
                  <option value="viewer">Viewer (Read-Only)</option>
                  <option value="owner">Co-Owner</option>
                </select>
              </div>
              <button class="btn btn-secondary btn-sm" onclick="addTeamMember()">
                Add Team Seat (Owner)
              </button>
            </div>
          </div>
        </div>

        <div id="tabGuide" class="tab-pane">
          {guide_tab_html}
        </div>
      </div>
    </main>
  </div>

  <script>
    const ROOM_TYPES = [
      'living_room', 'kitchen', 'kitchen_living', 'master_bedroom', 'bedroom',
      'bathroom', 'ensuite', 'wc', 'corridor', 'utility', 'veranda', 'storage',
      'open_office', 'meeting_room', 'server_room', 'retail_floor',
      'warehouse', 'plant_room', 'stairwell', 'lift_shaft'
    ];
    const LAYER_ROLES = ['auto', 'rooms', 'walls', 'doors', 'windows', 'fixtures', 'text', 'db'];

    const circuitOverrides = {{}};
    const roomTypeOverrides = {{}};
    let dbLocationOverride = null;
    let meterLocationOverride = null;
    let earthPitLocationOverride = null;
    let telecomLocationOverride = null;
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

    function updateAnchorBtnLabel() {{
      if (dbRelocateMode) return;
      const sel = document.getElementById('anchorTargetSel');
      const lbl = sel ? sel.options[sel.selectedIndex].text.replace('Anchor: ', '') : 'DB1';
      const shortLbl = lbl.startsWith('DB1') ? 'DB1' : lbl;
      const btn = document.getElementById('btnDbMode');
      if (btn) btn.textContent = `Relocate ${{shortLbl}} on Floorplan`;
    }}

    function toggleDbMode() {{
      dbRelocateMode = !dbRelocateMode;
      const btn = document.getElementById('btnDbMode');
      const box = document.getElementById('floorplanSvg');
      const sel = document.getElementById('anchorTargetSel');
      const lbl = sel ? sel.options[sel.selectedIndex].text.replace('Anchor: ', '') : 'DB1';
      const shortLbl = lbl.startsWith('DB1') ? 'DB1' : lbl;
      btn.className = dbRelocateMode ? 'btn btn-active btn-sm' : 'btn btn-secondary btn-sm';
      btn.textContent = dbRelocateMode
        ? `Click Floorplan to Place ${{shortLbl}} (Cancel)`
        : `Relocate ${{shortLbl}} on Floorplan`;
      box.classList.toggle('db-mode', dbRelocateMode);
    }}

    function resetVisualOverrides() {{
      Object.keys(roomTypeOverrides).forEach(k => delete roomTypeOverrides[k]);
      dbLocationOverride = null;
      meterLocationOverride = null;
      earthPitLocationOverride = null;
      telecomLocationOverride = null;
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
        tender_boq_blank_rates: document.getElementById('tenderBoqChk').checked,
        apply_corridor_grouping: document.getElementById('corridorCgChk').checked,
        include_tt_main_rcd_s: document.getElementById('ttMainRcdChk').checked,
        circuit_overrides: circuitOverrides,
        room_type_overrides: roomTypeOverrides
      }};
      const briefEvRaw = document.getElementById('briefEvKw').value;
      ov.residential_brief = {{
        hvac_mode: document.getElementById('briefHvac').value,
        conduit_drafting_style: document.getElementById('briefConduitStyle').value,
        earthing_electrode_type: document.getElementById('briefEarthing').value,
        building_height_m: parseFloat(document.getElementById('briefHeightM').value || '7.5'),
        plot_width_m: parseFloat(document.getElementById('briefPlotW').value || '18'),
        plot_length_m: parseFloat(document.getElementById('briefPlotL').value || '24'),
        include_electric_shutters: document.getElementById('briefShutters').checked,
        include_elv: document.getElementById('briefElv').checked,
        include_smoke_detectors: document.getElementById('briefSmoke').checked,
        include_kitchen_fcus: document.getElementById('briefKitchenFcus').checked,
        include_water_pumps: document.getElementById('briefPumps').checked,
        include_gate_motor: document.getElementById('briefGate').checked,
        include_site_works: document.getElementById('briefSite').checked,
        include_pool: document.getElementById('briefPool').checked,
        include_knx: document.getElementById('briefKnx').checked,
      }};
      if (briefEvRaw !== '') {{
        ov.residential_brief.ev_charger_kw = parseFloat(briefEvRaw);
      }}
      const vatRate = parseFloat(document.getElementById('vatRateSel').value || '0.19');
      if (Math.abs(vatRate - 0.19) > 1e-6) {{
        ov.boq_rate_overrides = {{ vat_rate: vatRate }};
      }}
      const ze = document.getElementById('zeRa').value;
      if (ze) ov.ze_or_ra_ohm = parseFloat(ze);
      const amb = document.getElementById('ambientC').value;
      if (amb) ov.ambient_temp_c = parseInt(amb, 10);
      const pv = document.getElementById('pvKva').value;
      if (pv) ov.pv_capacity_kva = parseFloat(pv);
      if (dbLocationOverride) ov.db_location_override_mm = dbLocationOverride;
      if (meterLocationOverride) ov.meter_location_override_mm = meterLocationOverride;
      if (earthPitLocationOverride) ov.earth_pit_location_override_mm = earthPitLocationOverride;
      if (telecomLocationOverride) ov.telecom_location_override_mm = telecomLocationOverride;

      const firm = document.getElementById('firmName').value.trim();
      const eng = document.getElementById('engName').value.trim();
      const etek = document.getElementById('etekReg').value.trim();
      if (firm || eng || etek) {{
        ov.branding = {{ firm_name: firm, engineer_name: eng, etek_reg_no: etek }};
      }}
      const eacMeta = {{
        district: (document.getElementById('eacDistrict').value || '').trim(),
        municipality: (document.getElementById('eacMunicipality').value || '').trim(),
        sheet_plan: (document.getElementById('eacSheetPlan').value || '').trim(),
        plot_no: (document.getElementById('eacPlotNo').value || '').trim(),
        building_permit_no: (document.getElementById('eacPermitNo').value || '').trim(),
        owner_name: (document.getElementById('eacOwner').value || '').trim(),
        eac_tariff: (document.getElementById('eacTariff').value || '').trim(),
        contractor_name: (document.getElementById('eacContractor').value || '').trim(),
        contractor_hmy_reg: (document.getElementById('eacHmyReg').value || '').trim(),
      }};
      if (Object.values(eacMeta).some(v => v.length > 0)) {{
        ov.eac_metadata = eacMeta;
      }}
      return ov;
    }}

    function collectMultiFloorMeta(fileList) {{
      const labels = [];
      const submains = {{}};
      for (let i = 0; i < fileList.length; i++) {{
        const lblEl = document.querySelector(`input[data-mf-lbl="${{i}}"]`);
        const lenEl = document.querySelector(`input[data-mf-len="${{i}}"]`);
        const defLbl = (i === 0) ? 'GF' : `${{i}}F`;
        const lbl = ((lblEl && lblEl.value) ? lblEl.value : defLbl).trim().toUpperCase() || defLbl;
        labels.push(lbl);
        if (lenEl && lenEl.value) {{
          const v = parseFloat(lenEl.value);
          if (!Number.isNaN(v) && v > 0) submains[lbl] = v;
        }}
      }}
      return {{ labels, submains }};
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

    function selectDrawingSheet(sheetKey) {{
      const valid = ['combined', 'lighting', 'power', 'elv'];
      const key = valid.includes(sheetKey) ? sheetKey : 'combined';
      window._cyelecActiveSheet = key;
      const sheets = window._cyelecSheets || {{}};
      document.getElementById('floorplanSvg').innerHTML =
        sheets[key] || sheets.combined || 'Run a solve to render floorplan overlay.';
      const btnMap = {{
        combined: 'btnSheetCombined',
        lighting: 'btnSheetLighting',
        power: 'btnSheetPower',
        elv: 'btnSheetElv',
      }};
      Object.entries(btnMap).forEach(([k, id]) => {{
        const el = document.getElementById(id);
        if (!el) return;
        el.classList.toggle('btn-primary', k === key);
        el.classList.toggle('btn-secondary', k !== key);
      }});
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
        ? (boq.tender_blank_rates
            ? 'Tender BOQ (Unpriced)'
            : `€${{boq.grand_total_eur.toLocaleString('en-US', {{minimumFractionDigits:2}})}}`)
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
          <td class="r">${{sm.downstream_ipf_ka != null
            ? sm.downstream_ipf_ka.toFixed(2) + ' kA' : '-'}}</td>
        </tr>`).join('');
        document.getElementById('subMainsBox').innerHTML = `<table>
          <thead><tr>
            <th>Sub-Board</th><th>Parent</th><th class="c">Floor</th><th>Upstream Protection</th>
            <th class="c">Sub-Main Cable</th><th class="r">Sub ΔV</th>
            <th class="r">Cumul. L/P ΔV</th><th class="r">Sub Ipf</th>
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

      window._cyelecSheets = {{
        combined: data.floorplan_svg || '',
        lighting: data.lighting_svg || data.floorplan_svg || '',
        power: data.power_svg || data.floorplan_svg || '',
        elv: data.elv_svg || data.floorplan_svg || '',
      }};
      selectDrawingSheet(window._cyelecActiveSheet || 'combined');
      document.getElementById('siteSvg').innerHTML =
        data.site_svg || 'E-104 Site & Earthing sheet unavailable.';
      document.getElementById('sldSvg').innerHTML = data.sld_svg || '';
      document.getElementById('riserSvg').innerHTML =
        data.riser_svg || 'E-202 Riser & DIN-Rail DB sheet unavailable.';
      document.getElementById('detailsSvg').innerHTML =
        data.details_svg || 'Details sheet unavailable.';

      if (boq && boq.items) {{
        const lang = document.getElementById('reportLang').value;
        const vatPct = Math.round((boq.vat_rate != null ? boq.vat_rate : 0.19) * 100);
        const blank = Boolean(boq.tender_blank_rates);
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
            <td class="r">${{blank ? '<em>Tender</em>' : '€' + it.unit_rate_eur.toFixed(2)}}</td>
            <td class="r">${{blank ? '<em>Formula</em>' : '€' + it.total_eur.toFixed(2)}}</td>
          </tr>`;
        }}).join('');
        const totals = blank
          ? `<tr><td colspan="7" class="r"><strong>Unpriced Tender BOQ Mode:
              Excel export leaves Column G blank with live =ROUND(F*G,2) &amp;
              ${{vatPct}}% Cyprus VAT formulas.</strong></td></tr>`
          : `<tr><td colspan="6" class="r"><strong>Subtotal (excl. VAT)</strong></td>
            <td class="r"><strong>€${{boq.subtotal_eur.toFixed(2)}}</strong></td></tr>
          <tr><td colspan="6" class="r"><strong>Cyprus VAT (${{vatPct}}%)</strong></td>
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

    // Delegated click listener for Interactive Visual Floorplan (Room Reclass & Anchor Relocation)
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
        const target = (document.getElementById('anchorTargetSel') || {{ value: 'db' }}).value;
        const ptMm = {{ x_mm: xMm, y_mm: yMm }};
        if (target === 'meter') meterLocationOverride = ptMm;
        else if (target === 'earth_pit') earthPitLocationOverride = ptMm;
        else if (target === 'telecom') telecomLocationOverride = ptMm;
        else dbLocationOverride = ptMm;
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
      let jwt = '';
      try {{ jwt = (sessionStorage.getItem('cyelec_jwt') || '').trim(); }} catch (_) {{}}
      if (jwt) h['Authorization'] = `Bearer ${{jwt}}`;
      const key = (document.getElementById('apiKeyInput').value || '').trim();
      if (key) h['X-API-Key'] = key;
      if (isJson) h['Content-Type'] = 'application/json';
      return h;
    }}

    document.getElementById('apiKeyInput').addEventListener('change', (ev) => {{
      try {{ sessionStorage.setItem('cyelec_api_key', ev.target.value.trim()); }} catch (_) {{}}
    }});

    // Multi-Floor selector + DXF Layer Mapping Wizard trigger on file select
    document.getElementById('dxfFile').addEventListener('change', async (ev) => {{
      const files = ev.target.files;
      const mfBox = document.getElementById('multiFloorBox');
      const mfRows = document.getElementById('multiFloorRows');
      const box = document.getElementById('layerWizard');
      const rows = document.getElementById('layerRows');
      Object.keys(layerRoleChoices).forEach(k => delete layerRoleChoices[k]);

      if (files.length > 1) {{
        mfRows.innerHTML = Array.from(files).map((fl, i) => {{
          const defLbl = (i === 0) ? 'GF' : `${{i}}F`;
          const defLen = (i === 0) ? 8 : (8 + i * 6);
          return `<div class="layer-row" style="grid-template-columns:1fr 56px 56px;">
            <span title="${{escapeHtml(fl.name)}}">${{escapeHtml(fl.name)}}</span>
            <input data-mf-lbl="${{i}}" value="${{defLbl}}" title="Floor Label" />
            <input data-mf-len="${{i}}" type="number" min="1" value="${{defLen}}"
              title="Sub-main m" />
          </div>`;
        }}).join('');
        mfBox.style.display = 'block';
      }} else {{
        mfBox.style.display = 'none';
        mfRows.innerHTML = '';
      }}

      const f = files[0];
      const fnLow = f ? f.name.toLowerCase() : '';
      if (!f || (!fnLow.endsWith('.dxf') && !fnLow.endsWith('.dwg'))) {{
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

    async function saveFirmDefaults() {{
      const status = document.getElementById('statusLine');
      const ov = collectOverrides();
      const vat = parseFloat(document.getElementById('vatRateSel').value || '0.19');
      const payload = {{
        report_language: ov.report_language,
        branding: ov.branding || null,
        boq_rate_overrides: {{ vat_rate: vat }}
      }};
      try {{
        const resp = await fetch('/v1/tenants/me', {{
          method: 'PUT',
          headers: authHeaders(true),
          body: JSON.stringify(payload)
        }});
        if (!resp.ok) throw new Error(await resp.text());
        status.textContent = 'Saved tenant firm branding, language & VAT defaults.';
      }} catch (err) {{
        status.textContent = `Save defaults failed: ${{err.message}}`;
      }}
    }}

    async function runSolve() {{
      const status = document.getElementById('statusLine');
      status.textContent = 'Running deterministic BS 7671 / EAC / NetworkX solve...';
      const fileInput = document.getElementById('dxfFile');
      const overrides = collectOverrides();

      try {{
        let resp;
        if (fileInput.files.length > 1) {{
          const fd = new FormData();
          for (const fl of fileInput.files) fd.append('files', fl);
          const meta = collectMultiFloorMeta(fileInput.files);
          fd.append('floors_json', JSON.stringify(meta.labels));
          if (Object.keys(meta.submains).length > 0) {{
            fd.append('submain_lengths_json', JSON.stringify(meta.submains));
          }}
          fd.append('overrides_json', JSON.stringify(overrides));
          const lm = collectLayerMapping();
          if (lm) fd.append('layer_mapping_json', JSON.stringify(lm));
          resp = await fetch('/v1/solve/dxf/multifloor', {{
            method: 'POST',
            headers: authHeaders(false),
            body: fd
          }});
        }} else if (fileInput.files.length === 1) {{
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
      if (fileInput.files.length > 1) {{
        const fd = new FormData();
        for (const fl of fileInput.files) fd.append('files', fl);
        const meta = collectMultiFloorMeta(fileInput.files);
        fd.append('floors_json', JSON.stringify(meta.labels));
        if (Object.keys(meta.submains).length > 0) {{
          fd.append('submain_lengths_json', JSON.stringify(meta.submains));
        }}
        fd.append('overrides_json', JSON.stringify(overrides));
        const lm = collectLayerMapping();
        if (lm) fd.append('layer_mapping_json', JSON.stringify(lm));
        resp = await fetch('/v1/solve/dxf/multifloor/bundle', {{
          method: 'POST',
          headers: authHeaders(false),
          body: fd
        }});
      }} else if (fileInput.files.length === 1) {{
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
      const fileInput = document.getElementById('dxfFile');
      let resp;
      if (fileInput.files.length === 1) {{
        const fd = new FormData();
        fd.append('file', fileInput.files[0]);
        fd.append('overrides_json', JSON.stringify(collectOverrides()));
        const lm = collectLayerMapping();
        if (lm) fd.append('layer_mapping_json', JSON.stringify(lm));
        fd.append('carry_forward', 'true');
        resp = await fetch(`/v1/projects/${{encodeURIComponent(pid)}}/revisions/dxf`, {{
          method: 'POST',
          headers: authHeaders(false),
          body: fd
        }});
      }} else {{
        const tmpl = document.getElementById('benchmarkSel').value;
        resp = await fetch(`/v1/projects/${{encodeURIComponent(pid)}}/revisions`, {{
          method: 'POST',
          headers: authHeaders(true),
          body: JSON.stringify({{
            benchmark_template: tmpl,
            overrides: collectOverrides(),
            carry_forward_overrides: true
          }})
        }});
      }}
      if (!resp.ok) {{ st.textContent = 'Save revision failed.'; return; }}
      const rec = await resp.json();
      st.textContent = `Saved Rev ${{rec.summary.revision_number}} cleanly.`;
      await loadProjects();
      sel.value = pid;
      await loadRevisions();
    }}

    async function exportTenantGdpr() {{
      const st = document.getElementById('wsStatus');
      const resp = await fetch('/v1/tenants/me/export', {{ headers: authHeaders(false) }});
      if (!resp.ok) {{ st.textContent = 'GDPR export failed: ' + await resp.text(); return; }}
      const blob = await resp.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = 'cyelec_gdpr_export.json';
      a.click();
      URL.revokeObjectURL(url);
      st.textContent = 'Downloaded GDPR Art. 20 portable JSON export.';
    }}

    async function eraseTenantGdpr() {{
      const st = document.getElementById('wsStatus');
      const msg = 'Permanently erase ALL projects, revisions & usage (GDPR Art. 17)?';
      if (!confirm(msg)) {{
        return;
      }}
      const resp = await fetch('/v1/tenants/me', {{
        method: 'DELETE',
        headers: authHeaders(false)
      }});
      if (!resp.ok) {{ st.textContent = 'GDPR erasure failed: ' + await resp.text(); return; }}
      st.textContent = 'Tenant workspace permanently erased (GDPR Art. 17).';
      document.getElementById('revListBox').innerHTML = '';
      document.getElementById('revDiffBox').innerHTML = '';
      await loadProjects();
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

    const AUTH_REQUIRED = {auth_req_js};
    const INVITE_CODE_REQUIRED = {invite_req_js};
    let authModalMode = 'login';

    function openAuthModal(mode) {{
      authModalMode = mode || 'login';
      switchAuthModalTab(authModalMode);
      document.getElementById('mError').textContent = '';
      document.getElementById('authModal').classList.add('open');
    }}

    function closeAuthModal() {{
      document.getElementById('authModal').classList.remove('open');
    }}

    function switchAuthModalTab(mode) {{
      authModalMode = mode;
      const isReg = (mode === 'register');
      document.getElementById('mTabLogin').classList.toggle('active', !isReg);
      document.getElementById('mTabReg').classList.toggle('active', isReg);
      document.getElementById('modalRegFields').style.display = isReg ? 'block' : 'none';
      document.getElementById('mSubmitBtn').textContent = isReg
        ? 'Create Firm Account'
        : 'Sign In';
      document.getElementById('mError').textContent = '';
    }}

    function applySignedInSession(data) {{
      if (data.access_token) {{
        try {{ sessionStorage.setItem('cyelec_jwt', data.access_token); }} catch (_) {{}}
      }}
      const t = data.tenant || {{}};
      const u = data.user || data.principal || {{}};
      const q = data.quota || null;
      if (t.tenant_id) document.getElementById('tenantId').value = t.tenant_id;
      if (t.branding) {{
        if (t.branding.firm_name) document.getElementById('firmName').value = t.branding.firm_name;
        if (t.branding.engineer_name) {{
          document.getElementById('engName').value = t.branding.engineer_name;
        }}
        if (t.branding.etek_reg_no) {{
          document.getElementById('etekReg').value = t.branding.etek_reg_no;
        }}
      }}
      if (t.report_language) document.getElementById('reportLang').value = t.report_language;
      const roleStr = (u.role || 'owner').toUpperCase();
      const emailStr = u.email || t.display_name || t.tenant_id || 'Signed In';
      const qStr = q ? ` · ${{q.solves_used}}/${{q.solves_limit}} solves` : '';
      document.getElementById('authBadge').textContent =
        `Signed in: ${{emailStr}} (${{roleStr}})${{qStr}}`;
      document.getElementById('hdrAccountBtn').textContent =
        `${{emailStr}} (${{roleStr}})${{qStr}}`;
      closeAuthModal();
    }}

    async function submitAuthModal() {{
      const errEl = document.getElementById('mError');
      errEl.textContent = '';
      const email = document.getElementById('mEmail').value.trim();
      const password = document.getElementById('mPass').value;
      const isReg = (authModalMode === 'register');
      const endpoint = isReg ? '/v1/auth/register' : '/v1/auth/login';
      const body = isReg
        ? {{
            firm_name: document.getElementById('mFirm').value.trim(),
            display_name: document.getElementById('mName').value.trim(),
            invite_code: (document.getElementById('mInvite').value || '').trim(),
            email,
            password
          }}
        : {{ email, password }};
      try {{
        const resp = await fetch(endpoint, {{
          method: 'POST',
          headers: {{ 'Content-Type': 'application/json' }},
          body: JSON.stringify(body)
        }});
        if (!resp.ok) {{
          let msg = await resp.text();
          try {{ msg = JSON.parse(msg).detail || msg; }} catch (_) {{}}
          errEl.textContent = msg;
          return;
        }}
        const data = await resp.json();
        applySignedInSession(data);
        await loadSubscriptionAndTeam();
        await runSolve();
      }} catch (err) {{
        errEl.textContent = err.message || 'Network error';
      }}
    }}

    async function firmRegister() {{
      const st = document.getElementById('wsStatus');
      const body = {{
        firm_name: document.getElementById('authFirm').value.trim(),
        display_name: (document.getElementById('authDisplayName').value || '').trim(),
        invite_code: (document.getElementById('authInvite').value || '').trim(),
        email: document.getElementById('authEmail').value.trim(),
        password: document.getElementById('authPass').value
      }};
      const resp = await fetch('/v1/auth/register', {{
        method: 'POST',
        headers: {{ 'Content-Type': 'application/json' }},
        body: JSON.stringify(body)
      }});
      if (!resp.ok) {{ st.textContent = `Register failed: ${{await resp.text()}}`; return; }}
      const data = await resp.json();
      applySignedInSession(data);
      st.textContent = `Registered firm ${{data.tenant.display_name}}.`;
      await loadSubscriptionAndTeam();
      await runSolve();
    }}

    async function firmLogin() {{
      const st = document.getElementById('wsStatus');
      const body = {{
        email: document.getElementById('authEmail').value.trim(),
        password: document.getElementById('authPass').value
      }};
      const resp = await fetch('/v1/auth/login', {{
        method: 'POST',
        headers: {{ 'Content-Type': 'application/json' }},
        body: JSON.stringify(body)
      }});
      if (!resp.ok) {{ st.textContent = `Sign in failed: ${{await resp.text()}}`; return; }}
      const data = await resp.json();
      applySignedInSession(data);
      st.textContent = `Welcome back, ${{data.user.display_name}}.`;
      await loadSubscriptionAndTeam();
      await runSolve();
    }}

    function firmLogout() {{
      try {{ sessionStorage.removeItem('cyelec_jwt'); }} catch (_) {{}}
      document.getElementById('authBadge').textContent = 'Signed out.';
      document.getElementById('hdrAccountBtn').textContent = 'Sign In / Register';
      document.getElementById('wsStatus').textContent = 'Signed out.';
      if (AUTH_REQUIRED) openAuthModal('login');
    }}

    async function changeMyPassword() {{
      const st = document.getElementById('wsStatus');
      const cur = document.getElementById('chgCurPass').value;
      const nxt = document.getElementById('chgNewPass').value;
      const resp = await fetch('/v1/auth/change-password', {{
        method: 'POST',
        headers: authHeaders(true),
        body: JSON.stringify({{ current_password: cur, new_password: nxt }})
      }});
      if (!resp.ok) {{ st.textContent = `Password change failed: ${{await resp.text()}}`; return; }}
      document.getElementById('chgCurPass').value = '';
      document.getElementById('chgNewPass').value = '';
      st.textContent = 'Your password has been updated.';
    }}

    async function resetMemberPassword(uid, email) {{
      const st = document.getElementById('wsStatus');
      const npw = prompt(`Enter new temporary password (8+ chars) for ${{email}}:`);
      if (!npw || npw.length < 8) return;
      const resp = await fetch(`/v1/team/members/${{encodeURIComponent(uid)}}/reset-password`, {{
        method: 'POST',
        headers: authHeaders(true),
        body: JSON.stringify({{ new_password: npw }})
      }});
      if (!resp.ok) {{ st.textContent = `Reset failed: ${{await resp.text()}}`; return; }}
      st.textContent = `Password reset for ${{email}}.`;
    }}

    async function loadSubscriptionAndTeam() {{
      const subBox = document.getElementById('subBox');
      const teamBox = document.getElementById('teamBox');
      const sResp = await fetch('/v1/billing/subscription', {{ headers: authHeaders(false) }});
      if (sResp.ok) {{
        const s = await sResp.json();
        const q = s.quota;
        subBox.innerHTML = `<strong>${{escapeHtml(s.plan.display_name)}}</strong>
          (<code>${{escapeHtml(s.plan.plan_id)}}</code> · ${{escapeHtml(s.subscription_status)}})
          <br/>Monthly Solves (${{escapeHtml(q.month_key)}}):
          <strong>${{q.solves_used}} / ${{q.solves_limit}}</strong>
          &nbsp;|&nbsp; Seats: <strong>${{s.seats_used}} / ${{s.seats_limit}}</strong>`;
      }}
      const tResp = await fetch('/v1/team/members', {{ headers: authHeaders(false) }});
      if (tResp.ok) {{
        const members = await tResp.json();
        teamBox.innerHTML = members.length
          ? members.map(m =>
              `<div style="display:flex;justify-content:space-between;align-items:center;
                           gap:6px;margin:3px 0;">
                <span><code>${{escapeHtml(m.role)}}</code> — ${{escapeHtml(m.email)}}
                (${{escapeHtml(m.display_name)}})</span>
                <button class="btn btn-secondary btn-sm"
                  onclick="resetMemberPassword('${{escapeHtml(m.user_id)}}',
                  '${{escapeHtml(m.email)}}')">Reset PW</button>
              </div>`
            ).join('')
          : 'No user accounts registered on this tenant yet.';
      }}
    }}

    async function addTeamMember() {{
      const st = document.getElementById('wsStatus');
      const body = {{
        email: document.getElementById('memEmail').value.trim(),
        display_name: document.getElementById('memName').value.trim(),
        password: document.getElementById('memPass').value,
        role: document.getElementById('memRole').value
      }};
      const resp = await fetch('/v1/team/members', {{
        method: 'POST',
        headers: authHeaders(true),
        body: JSON.stringify(body)
      }});
      if (!resp.ok) {{ st.textContent = `Add seat failed: ${{await resp.text()}}`; return; }}
      st.textContent = `Added ${{body.role}} seat for ${{body.email}}.`;
      await loadSubscriptionAndTeam();
    }}

    async function startCheckout(planId) {{
      const st = document.getElementById('wsStatus');
      const resp = await fetch('/v1/billing/checkout', {{
        method: 'POST',
        headers: authHeaders(true),
        body: JSON.stringify({{ plan_id: planId }})
      }});
      if (!resp.ok) {{ st.textContent = `Checkout unavailable: ${{await resp.text()}}`; return; }}
      const data = await resp.json();
      if (data.checkout_url) window.open(data.checkout_url, '_blank', 'noopener');
    }}

    async function openBillingPortal() {{
      const st = document.getElementById('wsStatus');
      const resp = await fetch('/v1/billing/portal', {{
        method: 'POST',
        headers: authHeaders(false)
      }});
      if (!resp.ok) {{ st.textContent = `Portal unavailable: ${{await resp.text()}}`; return; }}
      const data = await resp.json();
      if (data.portal_url) window.open(data.portal_url, '_blank', 'noopener');
    }}

    window.addEventListener('DOMContentLoaded', async () => {{
      let savedJwt = '';
      let savedKey = '';
      try {{
        savedKey = (sessionStorage.getItem('cyelec_api_key') || '').trim();
        if (savedKey) document.getElementById('apiKeyInput').value = savedKey;
        savedJwt = (sessionStorage.getItem('cyelec_jwt') || '').trim();
      }} catch (_) {{}}

      if (savedJwt) {{
        const meResp = await fetch('/v1/auth/me', {{ headers: authHeaders(false) }});
        if (meResp.ok) {{
          const me = await meResp.json();
          applySignedInSession(me);
          await loadSubscriptionAndTeam();
          await runSolve();
          return;
        }}
        try {{ sessionStorage.removeItem('cyelec_jwt'); }} catch (_) {{}}
      }}

      if (AUTH_REQUIRED && !savedKey) {{
        document.getElementById('statusLine').textContent =
          'Sign in or create your firm account to start designing.';
        openAuthModal('login');
        return;
      }}
      await runSolve();
    }});
  </script>
</body>
</html>
"""
