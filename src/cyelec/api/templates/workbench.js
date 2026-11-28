    const UI_LANG = "__UI_LANG__";
    const T = __JS_I18N_JSON__;
    const ROOM_TYPES = [
      'living_room', 'kitchen', 'kitchen_living', 'master_bedroom', 'bedroom',
      'bathroom', 'ensuite', 'wc', 'corridor', 'utility', 'veranda', 'storage',
      'open_office', 'meeting_room', 'server_room', 'retail_floor',
      'warehouse', 'plant_room', 'stairwell', 'lift_shaft'
    ];
    const LAYER_ROLES = ['auto', 'rooms', 'walls', 'doors', 'windows', 'fixtures', 'text', 'db'];

    const circuitOverrides = {};
    const roomTypeOverrides = {};
    let dbLocationOverride = null;
    let meterLocationOverride = null;
    let earthPitLocationOverride = null;
    let telecomLocationOverride = null;
    let dbRelocateMode = false;
    let selectedRoomId = null;
    const layerRoleChoices = {};
    const svgZoomState = {};
    let currentFsFileName = 'drawing.svg';
    let isSolving = false;
    let isDownloading = false;

    function escapeHtml(value) {
      return String(value == null ? '' : value)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
    }

    function setStatus(msg, kind) {
      const el = document.getElementById('statusLine');
      if (!el) return;
      el.textContent = msg;
      const cls = kind === 'err' ? ' status-err' : (kind === 'ok' ? ' status-ok' : '');
      el.className = 'status-line' + cls;
    }

    function openTab(id, btn) {
      document.querySelectorAll('.tab-pane').forEach(p => p.classList.remove('active'));
      document.querySelectorAll('.tab').forEach(b => b.classList.remove('active'));
      document.getElementById(id).classList.add('active');
      btn.classList.add('active');
    }

    function applyZoomToBox(boxId) {
      const pct = svgZoomState[boxId] || 100;
      const box = document.getElementById(boxId);
      const lbl = document.getElementById('zoomLbl_' + boxId);
      if (lbl) lbl.textContent = pct + '%';
      if (!box) return;
      const svg = box.querySelector('svg');
      if (svg) {
        svg.style.width = pct + '%';
        svg.style.maxWidth = 'none';
      }
    }

    function zoomSvg(boxId, delta) {
      const cur = svgZoomState[boxId] || 100;
      svgZoomState[boxId] = Math.min(350, Math.max(50, cur + delta));
      applyZoomToBox(boxId);
    }

    function resetZoomSvg(boxId) {
      svgZoomState[boxId] = 100;
      applyZoomToBox(boxId);
    }

    function openFullscreenSvg(boxId, title) {
      const src = document.getElementById(boxId);
      if (!src || !src.querySelector('svg')) return;
      document.getElementById('fsTitle').textContent = title || 'Drawing Sheet Viewer';
      const body = document.getElementById('fsBody');
      body.innerHTML = src.innerHTML;
      currentFsFileName = (boxId || 'sheet') + '.svg';
      svgZoomState.fsBody = 100;
      applyZoomToBox('fsBody');
      document.getElementById('fsModal').classList.add('open');
    }

    function closeFullscreenSvg() {
      document.getElementById('fsModal').classList.remove('open');
    }

    function downloadSvgBox(boxId, filename) {
      const box = document.getElementById(boxId);
      if (!box) return;
      const svg = box.querySelector('svg');
      if (!svg) return;
      const clone = svg.cloneNode(true);
      clone.style.width = '';
      clone.style.maxWidth = '';
      const raw = new XMLSerializer().serializeToString(clone);
      const blob = new Blob([raw], { type: 'image/svg+xml;charset=utf-8' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = filename || 'drawing.svg';
      a.click();
      URL.revokeObjectURL(url);
    }

    function downloadCurrentFsSvg() {
      downloadSvgBox('fsBody', currentFsFileName);
    }

    function updatePendingEditsBar() {
      const cnt = Object.keys(circuitOverrides).length;
      const bar = document.getElementById('pendingEditsBar');
      const txt = document.getElementById('pendingEditsTxt');
      if (!bar || !txt) return;
      if (cnt > 0) {
        txt.textContent = `${cnt} ${T.pendingPrefix} ${T.pendingSuffix}`;
        bar.style.display = 'flex';
      } else {
        bar.style.display = 'none';
      }
    }

    function clearCircuitEdits() {
      Object.keys(circuitOverrides).forEach(k => delete circuitOverrides[k]);
      updatePendingEditsBar();
      runSolve();
    }

    function clearDesignOverrides() {
      Object.keys(circuitOverrides).forEach(k => delete circuitOverrides[k]);
      Object.keys(roomTypeOverrides).forEach(k => delete roomTypeOverrides[k]);
      dbLocationOverride = null;
      meterLocationOverride = null;
      earthPitLocationOverride = null;
      telecomLocationOverride = null;
      selectedRoomId = null;
      const reb = document.getElementById('roomEditBox');
      if (reb) reb.style.display = 'none';
      if (dbRelocateMode) toggleDbMode();
      updatePendingEditsBar();
    }

    function clearUploadedFiles() {
      const fi = document.getElementById('dxfFile');
      if (fi) fi.value = '';
      document.getElementById('fileStatusRow').style.display = 'none';
      document.getElementById('fileStatusTxt').textContent = '';
      document.getElementById('multiFloorBox').style.display = 'none';
      document.getElementById('multiFloorRows').innerHTML = '';
      document.getElementById('layerWizard').style.display = 'none';
      document.getElementById('layerRows').innerHTML = '';
      Object.keys(layerRoleChoices).forEach(k => delete layerRoleChoices[k]);
      clearDesignOverrides();
      runSolve();
    }

    function _anchorShortLabel() {
      const sel = document.getElementById('anchorTargetSel');
      const raw = sel ? sel.options[sel.selectedIndex].text : 'DB1';
      const lbl = raw.replace(/^Anchor:\s*/i, '').replace(/^Σημείο:\s*/i, '');
      return lbl.startsWith('DB1') ? 'DB1' : lbl;
    }

    function updateAnchorBtnLabel() {
      const shortLbl = _anchorShortLabel();
      const btn = document.getElementById('btnDbMode');
      if (!btn) return;
      btn.textContent = dbRelocateMode
        ? `${T.clickPlacePrefix} ${shortLbl} ${T.cancelTag}`
        : `${T.relocatePrefix} ${shortLbl} ${T.relocateSuffix}`;
    }

    function toggleDbMode() {
      dbRelocateMode = !dbRelocateMode;
      const btn = document.getElementById('btnDbMode');
      const box = document.getElementById('floorplanSvg');
      btn.className = dbRelocateMode ? 'btn btn-active btn-sm' : 'btn btn-secondary btn-sm';
      updateAnchorBtnLabel();
      box.classList.toggle('db-mode', dbRelocateMode);
    }

    function resetVisualOverrides() {
      Object.keys(roomTypeOverrides).forEach(k => delete roomTypeOverrides[k]);
      dbLocationOverride = null;
      meterLocationOverride = null;
      earthPitLocationOverride = null;
      telecomLocationOverride = null;
      selectedRoomId = null;
      document.getElementById('roomEditBox').style.display = 'none';
      if (dbRelocateMode) toggleDbMode();
      runSolve();
    }

    function applyRoomOverride() {
      if (!selectedRoomId) return;
      roomTypeOverrides[selectedRoomId] = document.getElementById('roomTypeSel').value;
      document.getElementById('roomEditBox').style.display = 'none';
      runSolve();
    }

    function collectLayerMapping() {
      const mapping = {
        rooms: [], walls: [], doors: [], windows: [], fixtures: [], text: [], db: []
      };
      let anyExplicit = false;
      for (const [lname, role] of Object.entries(layerRoleChoices)) {
        if (role && role !== 'auto' && mapping[role]) {
          mapping[role].push(lname);
          anyExplicit = true;
        }
      }
      return anyExplicit ? mapping : null;
    }

    function collectOverrides() {
      const ov = {
        eac_profile: document.getElementById('eacProfile').value,
        earthing_system: document.getElementById('earthing').value,
        report_language: document.getElementById('reportLang').value,
        tender_boq_blank_rates: document.getElementById('tenderBoqChk').checked,
        apply_corridor_grouping: document.getElementById('corridorCgChk').checked,
        include_tt_main_rcd_s: document.getElementById('ttMainRcdChk').checked,
        circuit_overrides: circuitOverrides,
        room_type_overrides: roomTypeOverrides
      };
      const briefEvRaw = document.getElementById('briefEvKw').value;
      ov.residential_brief = {
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
        include_decorative_wall_sconces: document.getElementById('briefWallSconces').checked,
        pv_module_wp: parseInt(document.getElementById('briefPvWp').value || '550', 10),
        battery_storage_kwh: parseFloat(document.getElementById('briefBatteryKwh').value || '0'),
        include_eps_backup: document.getElementById('briefEpsBackup').checked,
        include_pool: document.getElementById('briefPool').checked,
        include_knx: document.getElementById('briefKnx').checked,
      };
      if (briefEvRaw !== '') {
        ov.residential_brief.ev_charger_kw = parseFloat(briefEvRaw);
      }
      const vatRate = parseFloat(document.getElementById('vatRateSel').value || '0.19');
      if (Math.abs(vatRate - 0.19) > 1e-6) {
        ov.boq_rate_overrides = { vat_rate: vatRate };
      }
      const ze = document.getElementById('zeRa').value;
      if (ze) ov.ze_or_ra_ohm = parseFloat(ze);
      const amb = document.getElementById('ambientC').value;
      if (amb) ov.ambient_temp_c = parseInt(amb, 10);
      const pv = document.getElementById('pvKva').value;
      if (pv) ov.pv_capacity_kva = parseFloat(pv);
      const shSel = document.getElementById('supplyHeadroomSel').value;
      if (shSel === 'headroom20') ov.min_supply_headroom_pct = 20.0;
      else if (shSel === 'min60') ov.min_main_breaker_a = 60;
      else if (shSel === 'min80') ov.min_main_breaker_a = 80;
      else if (shSel === 'min100') ov.min_main_breaker_a = 100;
      if (dbLocationOverride) ov.db_location_override_mm = dbLocationOverride;
      if (meterLocationOverride) ov.meter_location_override_mm = meterLocationOverride;
      if (earthPitLocationOverride) ov.earth_pit_location_override_mm = earthPitLocationOverride;
      if (telecomLocationOverride) ov.telecom_location_override_mm = telecomLocationOverride;

      const firm = document.getElementById('firmName').value.trim();
      const eng = document.getElementById('engName').value.trim();
      const etek = document.getElementById('etekReg').value.trim();
      if (firm || eng || etek) {
        ov.branding = { firm_name: firm, engineer_name: eng, etek_reg_no: etek };
      }
      const eacMeta = {
        district: (document.getElementById('eacDistrict').value || '').trim(),
        municipality: (document.getElementById('eacMunicipality').value || '').trim(),
        sheet_plan: (document.getElementById('eacSheetPlan').value || '').trim(),
        plot_no: (document.getElementById('eacPlotNo').value || '').trim(),
        building_permit_no: (document.getElementById('eacPermitNo').value || '').trim(),
        owner_name: (document.getElementById('eacOwner').value || '').trim(),
        eac_premises_no: (document.getElementById('eacPremisesNo').value || '').trim(),
        eac_tariff: (document.getElementById('eacTariff').value || '').trim(),
        contractor_name: (document.getElementById('eacContractor').value || '').trim(),
        contractor_hmy_reg: (document.getElementById('eacHmyReg').value || '').trim(),
        supply_connection_type: document.getElementById('eacSupplyConn').value || 'underground',
        inspection_date: (document.getElementById('eacInspDate').value || '').trim(),
      };
      const optFloat = (id) => {
        const v = (document.getElementById(id).value || '').trim();
        return v === '' ? null : parseFloat(v);
      };
      const mZe = optFloat('measZe');
      const mRa = optFloat('measRa');
      const mMb = optFloat('measMainBond');
      const mIr = optFloat('measIr');
      const mR1 = optFloat('measRcd1x');
      const mR5 = optFloat('measRcd5x');
      if (mZe !== null) eacMeta.measured_ze_ohm = mZe;
      if (mRa !== null) eacMeta.measured_ra_ohm = mRa;
      if (mMb !== null) eacMeta.measured_main_bonding_ohm = mMb;
      if (mIr !== null) eacMeta.measured_ir_mohm = mIr;
      if (mR1 !== null) eacMeta.measured_rcd_1x_ms = mR1;
      if (mR5 !== null) eacMeta.measured_rcd_5x_ms = mR5;
      const hasMeta = Object.entries(eacMeta).some(
        ([k, v]) => (k === 'supply_connection_type' ? v !== 'underground' : v !== '' && v !== null)
      );
      if (hasMeta) {
        ov.eac_metadata = eacMeta;
      }
      return ov;
    }

    function collectMultiFloorMeta(fileList) {
      const labels = [];
      const submains = {};
      for (let i = 0; i < fileList.length; i++) {
        const lblEl = document.querySelector(`input[data-mf-lbl="${i}"]`);
        const lenEl = document.querySelector(`input[data-mf-len="${i}"]`);
        const defLbl = (i === 0) ? 'GF' : `${i}F`;
        const lbl = ((lblEl && lblEl.value) ? lblEl.value : defLbl).trim().toUpperCase() || defLbl;
        labels.push(lbl);
        if (lenEl && lenEl.value) {
          const v = parseFloat(lenEl.value);
          if (!Number.isNaN(v) && v > 0) submains[lbl] = v;
        }
      }
      return { labels, submains };
    }

    function setCircuitOverride(cid, field, val) {
      if (!circuitOverrides[cid]) circuitOverrides[cid] = { circuit_id: cid };
      if (val === '' || val === 'AUTO') {
        delete circuitOverrides[cid][field];
      } else {
        circuitOverrides[cid][field] = (field === 'grouping_count') ? parseInt(val, 10) : val;
      }
      if (Object.keys(circuitOverrides[cid]).length <= 1) delete circuitOverrides[cid];
      updatePendingEditsBar();
    }

    function selectDrawingSheet(sheetKey) {
      const valid = ['combined', 'lighting', 'heatmap', 'power', 'elv'];
      const key = valid.includes(sheetKey) ? sheetKey : 'combined';
      window._cyelecActiveSheet = key;
      const sheets = window._cyelecSheets || {};
      document.getElementById('floorplanSvg').innerHTML =
        sheets[key] || sheets.combined || 'Run a solve to render floorplan overlay.';
      applyZoomToBox('floorplanSvg');
      const btnMap = {
        combined: 'btnSheetCombined',
        lighting: 'btnSheetLighting',
        heatmap: 'btnSheetHeatmap',
        power: 'btnSheetPower',
        elv: 'btnSheetElv',
      };
      Object.entries(btnMap).forEach(([k, id]) => {
        const el = document.getElementById(id);
        if (!el) return;
        el.classList.toggle('btn-primary', k === key);
        el.classList.toggle('btn-secondary', k !== key);
      });
    }

    function renderStage5EngineeringSummary(d) {
      const box = document.getElementById('stage5EngSummary');
      if (!box) return;
      const chips = [];
      const lps = d.lightning_risk;
      if (lps) {
        const cls = lps.lps_required ? 'b-warn' : 'b-pass';
        const vTxt = lps.lps_required ? T.lpsReq : T.noLps;
        chips.push(
          `<div class="s5-chip"><strong>IEC 62305-2:</strong>
            <span class="badge ${cls}">${escapeHtml(vTxt)}</span>
            R1=${Number(lps.risk_r1).toExponential(2)} ·
            ${escapeHtml(lps.spd_requirement)}</div>`
        );
      }
      const boards = d.din_rail_boards || [];
      if (boards.length > 0) {
        const bStr = boards.map(b => {
          const bid = escapeHtml(b.board_id);
          const cap = `${b.total_modules_used}/${b.total_modules_capacity}M`;
          return `<code>${bid}</code> ${cap} (${Math.round(b.spare_pct)}% ${T.spare})`;
        }).join(' · ');
        const allSel = boards.every(b => b.selectivity_pass);
        chips.push(
          `<div class="s5-chip"><strong>${T.dinDbs}:</strong> ${bStr}
            <span class="badge ${allSel ? 'b-pass' : 'b-fail'}">
              ${allSel ? T.selPass : T.selCheck}
            </span></div>`
        );
      }
      const pvs = d.pv_storage_design;
      if (pvs) {
        const pvOk = pvs.voc_cold_pass && pvs.mppt_window_pass && pvs.dc_vd_pass;
        const epsTag = pvs.eps_backup_enabled ? ' + EPS' : '';
        const battTxt = pvs.battery_enabled
          ? (` · BESS ${pvs.battery_nominal_kwh} kWh LFP `
             + `(${pvs.battery_bus_voltage_v}V${epsTag})`)
          : T.storageReady;
        chips.push(
          `<div class="s5-chip"><strong>${T.pvBessHdr}</strong>
            ${pvs.module_count}×${pvs.module_wp}Wp = ${pvs.array_dc_kwp} kWp
            (${pvs.strings_count}×${pvs.modules_per_string}s,
            Voc 0°C=${pvs.string_voc_cold_0c_v}V,
            ΔV_DC=${pvs.dc_voltage_drop_pct}%)${escapeHtml(battTxt)}
            <span class="badge ${pvOk ? 'b-pass' : 'b-warn'}">
              ${pvOk ? 'DC PASS' : T.check}
            </span></div>`
        );
      }
      const lumens = d.lumen_checks || [];
      let luxHtml = '';
      if (lumens.length > 0) {
        const okCnt = lumens.filter(x => x.compliant).length;
        const lCls = (okCnt === lumens.length) ? 'b-pass' : 'b-warn';
        const lRows = lumens.map(lc => {
          const cnt = Number(lc.luminaire_count || 0);
          const watts = Number(lc.watts_per_fixture || 0);
          const minMax = (lc.min_lux != null && lc.max_lux != null)
            ? `${Number(lc.min_lux).toFixed(0)} / ${Number(lc.max_lux).toFixed(0)} lx`
            : '—';
          const u0Tgt = Number(lc.u0_target || 0.4).toFixed(2);
          const u0Str = (lc.uniformity_u0 != null)
            ? `${Number(lc.uniformity_u0).toFixed(2)} (≥${u0Tgt})`
            : '—';
          const passAll = Boolean(lc.compliant && (lc.u0_compliant !== false));
          return `<tr>
          <td>${escapeHtml(lc.room_label)}</td>
          <td class="r">${Number(lc.area_m2).toFixed(1)} m²</td>
          <td class="c">${cnt} × ${watts.toFixed(0)}W</td>
          <td class="r">${lc.target_lux} lx</td>
          <td class="r"><strong>${Number(lc.achieved_lux).toFixed(0)} lx</strong></td>
          <td class="r">${minMax}</td>
          <td class="r">${u0Str}</td>
          <td class="c"><span class="badge ${passAll ? 'b-pass' : 'b-warn'}">
            ${passAll ? T.pass : T.check}</span></td>
        </tr>`;
        }).join('');
        luxHtml = `<details style="margin-top:6px;border:1px solid var(--border);
            border-radius:5px;padding:6px 10px;background:#f8fafc;">
          <summary style="cursor:pointer;font-weight:700;font-size:11.5px;color:var(--navy);">
            ${T.photTitle}:
            <span class="badge ${lCls}">${okCnt}/${lumens.length} ${T.compliant}</span>
          </summary>
          <div class="tbl-wrap" style="margin-top:8px;">
            <table><thead><tr>
              <th>${T.thRoom}</th><th class="r">${T.thArea}</th>
              <th class="c">${T.thLums}</th><th class="r">${T.thTarget}</th>
              <th class="r">E_avg</th><th class="r">E_min / E_max</th>
              <th class="r">U₀ (Emin/Eavg)</th><th class="c">${T.thCheck}</th>
            </tr></thead><tbody>${lRows}</tbody></table>
          </div>
        </details>`;
      }
      const strip = chips.length ? `<div class="s5-strip">${chips.join('')}</div>` : '';
      box.innerHTML = strip + luxHtml;
    }

    function renderResult(data) {
      const d = data.result.design;
      const sp = data.result.spatial;
      const eac = d.eac_compliance;
      const pb = d.phase_balance;
      const boq = d.boq;

      const utilPct = Number(eac.supply_tier_utilization_pct || 0);
      const utilTag = utilPct > 0 ? ` (${utilPct.toFixed(1)}%)` : '';
      const kSupEl = document.getElementById('kSupply');
      kSupEl.textContent = `${eac.recommended_supply_tier}${utilTag}`;
      kSupEl.style.color = utilPct >= 92 ? '#b45309' : '';
      const divKva = eac.total_diversified_kva.toFixed(2);
      const conKva = eac.total_connected_kva.toFixed(2);
      document.getElementById('kKva').textContent = `${divKva} / ${conKva} kVA`;
      document.getElementById('kBal').textContent = pb
        ? `${pb.imbalance_pct.toFixed(2)}% | ${sp.total_conduit_m.toFixed(1)} m`
        : `1P | ${sp.total_conduit_m.toFixed(1)} m`;
      const isBlankBoq = Boolean(boq && (boq.is_tender_blank_rates || boq.tender_blank_rates));
      document.getElementById('kBoq').textContent = boq
        ? (isBlankBoq
            ? T.unpricedBoq
            : `€${boq.grand_total_eur.toLocaleString('en-US', {minimumFractionDigits:2})}`)
        : '-';

      const blk = d.review_flags.filter(f => f.severity === 'blocker').length;
      const wrn = d.review_flags.filter(f => f.severity === 'warning').length;
      const inf = d.review_flags.filter(f => f.severity === 'info').length;
      document.getElementById('kFlags').textContent =
        `${blk} ${T.blk} / ${wrn} ${T.wrn} / ${inf} ${T.info}`;
      const tabFlagsBtn = document.getElementById('tabBtnFlags');
      if (tabFlagsBtn) {
        tabFlagsBtn.textContent = `${T.flagsTab} (${blk}B/${wrn}W)`;
      }

      if (pb) {
        const mx = Math.max(pb.max_phase_current_a, 1);
        const colors = { L1: '#8B4513', L2: '#1A202C', L3: '#718096' };
        document.getElementById('balanceBars').innerHTML = ['L1','L2','L3'].map(p => {
          const val = pb.phase_currents_a[p];
          const pct = Math.min(100, (val / mx) * 100);
          const st = `width:${pct}%;background:${colors[p]};`;
          const bar = `<div class="bar-fill" style="${st}"></div>`;
          return `<div class="bar-row"><strong>${p}</strong>
            <div class="bar-track">${bar}</div>
            <span class="r">${val.toFixed(2)} A</span></div>`;
        }).join('');
      } else {
        document.getElementById('balanceBars').innerHTML = '';
      }

      renderStage5EngineeringSummary(d);

      if (d.sub_mains && d.sub_mains.length > 0) {
        const rows = d.sub_mains.map(sm => `<tr>
          <td><strong>${escapeHtml(sm.board_id)}</strong></td>
          <td>${escapeHtml(sm.parent_board_id)}</td>
          <td class="c">${escapeHtml(sm.floor)}</td>
          <td>${escapeHtml(sm.device_label)}</td>
          <td class="c">${sm.cable_mm2}/${sm.cpc_mm2} mm² (${sm.length_m} m)</td>
          <td class="r">${sm.voltage_drop_pct.toFixed(2)}%</td>
          <td class="r">${sm.cumulative_lighting_vd_pct.toFixed(2)}% /
            ${sm.cumulative_power_vd_pct.toFixed(2)}%</td>
          <td class="r">${sm.downstream_ipf_ka != null
            ? sm.downstream_ipf_ka.toFixed(2) + ' kA' : '-'}</td>
        </tr>`).join('');
        document.getElementById('subMainsBox').innerHTML = `<div class="tbl-wrap"><table>
          <thead><tr>
            <th>${T.subBoard}</th><th>${T.parent}</th><th class="c">${T.floor}</th>
            <th>${T.upProt}</th><th class="c">${T.subCable}</th><th class="r">Sub ΔV</th>
            <th class="r">${T.cumulLpVd}</th><th class="r">Sub Ipf</th>
          </tr></thead><tbody>${rows}</tbody></table></div>`;
      } else {
        document.getElementById('subMainsBox').innerHTML = '';
      }

      document.getElementById('circuitsBody').innerHTML = d.circuits.map(c => {
        const s = c.spec;
        const cid = escapeHtml(s.circuit_id);
        const bid = escapeHtml(s.board_id || 'DB1');
        const desc = escapeHtml(s.description);
        const devLbl = escapeHtml(c.device_label);
        const phVal = escapeHtml(c.phase);
        const ov = circuitOverrides[s.circuit_id] || {};
        const curPhase = ov.phase || 'AUTO';
        const curCg = Number(ov.grouping_count || s.grouping_count);
        const phSelect = s.is_three_phase ? '3P' : `
          <select class="phase-sel" data-cid="${cid}" data-field="phase">
            <option value="AUTO" ${curPhase==='AUTO'?'selected':''}>
              ${T.autoPhase} (${phVal})
            </option>
            <option value="L1" ${curPhase==='L1'?'selected':''}>L1</option>
            <option value="L2" ${curPhase==='L2'?'selected':''}>L2</option>
            <option value="L3" ${curPhase==='L3'?'selected':''}>L3</option>
          </select>`;
        const cgInput = `<input class="mini-input" type="number" min="1" max="20"
          data-cid="${cid}" data-field="grouping_count" value="${curCg}" />`;
        const bCls = c.overall_pass ? 'b-pass' : 'b-fail';
        const stTxt = c.overall_pass ? T.pass : T.fail;
        return `<tr>
          <td><strong>${cid}</strong></td>
          <td><code>${bid}</code></td>
          <td>${desc}</td>
          <td>${phSelect}</td>
          <td>${cgInput}</td>
          <td class="r">${s.design_current_ib_a.toFixed(2)}</td>
          <td>${devLbl}</td>
          <td class="c">${c.cable_mm2}/${c.cpc_mm2}</td>
          <td class="r">${s.length_m.toFixed(1)}</td>
          <td class="r">${c.iz_derated_a.toFixed(1)}/${c.required_iz_a.toFixed(1)}</td>
          <td class="r">${c.voltage_drop_pct.toFixed(2)}%</td>
          <td class="c"><span class="badge ${bCls}">${stTxt}</span></td>
        </tr>`;
      }).join('');
      updatePendingEditsBar();

      window._cyelecSheets = {
        combined: data.floorplan_svg || '',
        lighting: data.lighting_svg || data.floorplan_svg || '',
        heatmap: data.lighting_heatmap_svg || data.lighting_svg || '',
        power: data.power_svg || data.floorplan_svg || '',
        elv: data.elv_svg || data.floorplan_svg || '',
      };
      selectDrawingSheet(window._cyelecActiveSheet || 'combined');
      document.getElementById('siteSvg').innerHTML =
        data.site_svg || 'E-104 Site & Earthing sheet unavailable.';
      applyZoomToBox('siteSvg');
      document.getElementById('sldSvg').innerHTML = data.sld_svg || '';
      applyZoomToBox('sldSvg');
      document.getElementById('riserSvg').innerHTML =
        data.riser_svg || 'E-202 Riser & DIN-Rail DB sheet unavailable.';
      applyZoomToBox('riserSvg');
      document.getElementById('selectivitySvg').innerHTML =
        data.selectivity_svg || 'E-203 Time-Current Selectivity sheet unavailable.';
      applyZoomToBox('selectivitySvg');
      document.getElementById('boardsSvg').innerHTML =
        data.boards_svg || 'E-204 Main &amp; Secondary Board Designs sheet unavailable.';
      applyZoomToBox('boardsSvg');
      document.getElementById('detailsSvg').innerHTML =
        data.details_svg || 'Details sheet unavailable.';
      applyZoomToBox('detailsSvg');

      if (boq && boq.items) {
        const lang = document.getElementById('reportLang').value;
        const vatPct = Math.round((boq.vat_rate != null ? boq.vat_rate : 0.19) * 100);
        const blank = Boolean(boq.is_tender_blank_rates || boq.tender_blank_rates);
        const SEC_TITLES_EL = {
          '01': 'ΕΝΟΤΗΤΑ 01 — Ηλεκτρικοί Πίνακες & Υλικό Διανομής',
          '02': 'ΕΝΟΤΗΤΑ 02 — Καλώδια, Σωληνώσεις & Σχάρες',
          '03': 'ΕΝΟΤΗΤΑ 03 — Φωτισμός & Διακόπτες',
          '04': 'ΕΝΟΤΗΤΑ 04 — Ρευματοδότες & Μόνιμα Εξαρτήματα',
          '05': 'ΕΝΟΤΗΤΑ 05 — Μηχανολογικά, Κλιματισμός, Ρολά & Αντλίες',
          '06': 'ΕΝΟΤΗΤΑ 06 — Ασθενή Ρεύματα (ELV), Ασφάλεια & Πυρανίχνευση',
          '07': 'ΕΝΟΤΗΤΑ 07 — Γείωση, Ισοδυναμικές & Αντικεραυνική Προστασία',
          '08': 'ΕΝΟΤΗΤΑ 08 — Εξωτερικά Έργα Οικοπέδου & Εκσκαφές',
          '09': 'ΕΝΟΤΗΤΑ 09 — Έξυπνο Σπίτι / Αυτοματισμός KNX',
          '10': 'ΕΝΟΤΗΤΑ 10 — Έλεγχοι, Δοκιμές & Τεκμηρίωση ΑΗΚ (Η.Μ.Υ. 58)',
        };
        const isElBoq = (lang === 'el' || UI_LANG === 'el');
        const SEC_TITLES = isElBoq ? SEC_TITLES_EL : T.boqSecTitles;
        const mapSec = (it) => {
          const cat = String(it.category || '').toLowerCase();
          const desc = String(it.description_en || '').toLowerCase();
          if (cat.includes('switchgear') || cat.includes('board')) return '01';
          if (cat.includes('cable') || cat.includes('containment') || cat.includes('conduit'))
            return '02';
          const litKeys = [
            'luminaire', 'switch', 'mirror light', 'dimmer', 'pendant', 'emergency', 'exhaust fan',
          ];
          if (cat.includes('lighting') || litKeys.some(k => desc.includes(k))) {
            const isFixed =
              desc.includes('water heater') ||
              desc.includes('cooker') ||
              desc.includes('shutter');
            if (isFixed) return desc.includes('shutter') ? '05' : '04';
            return '03';
          }
          const mechKeys = [
            'split a/c', 'shutter', 'heat pump', 'vrv', 'pump', 'gate', 'pool',
            'ev charger', 'pv ac',
          ];
          if (
            cat.includes('mechanical') ||
            cat.includes('plant') ||
            mechKeys.some(k => desc.includes(k))
          ) return '05';
          if (
            cat.includes('elv') || cat.includes('security') ||
            desc.includes('fire alarm') || desc.includes('smoke')
          ) return '06';
          if (cat.includes('earthing') || desc.includes('lightning') || desc.includes('bonding'))
            return '07';
          if (cat.includes('site') || desc.includes('trench') || desc.includes('bollard'))
            return '08';
          if (cat.includes('knx')) return '09';
          if (cat.includes('testing') || cat.includes('commissioning')) return '10';
          return '04';
        };
        const groups = new Map();
        for (const it of boq.items) {
          const code = mapSec(it);
          if (!groups.has(code)) groups.set(code, []);
          groups.get(code).push(it);
        }
        const rowsHtml = [];
        const SEC_ORDER = ['01', '02', '03', '04', '05', '06', '07', '08', '09', '10'];
        for (const code of SEC_ORDER) {
          const secItems = groups.get(code);
          if (!secItems || !secItems.length) continue;
          const secHdr = SEC_TITLES[code];
          rowsHtml.push(
            `<tr class="boq-sec-row"><td colspan="7">${escapeHtml(secHdr)}</td></tr>`
          );
          let secSum = 0;
          for (const it of secItems) {
            secSum += Number(it.total_eur || 0);
            const txt = lang === 'el'
              ? it.description_el
              : (lang === 'bilingual'
                  ? `${it.description_en} / ${it.description_el}`
                  : it.description_en);
            const rateCell = blank
              ? `<em>${escapeHtml(isElBoq ? 'Προσφορά' : T.tenderRate)}</em>`
              : '€' + it.unit_rate_eur.toFixed(2);
            const totCell = blank
              ? `<em>${escapeHtml(isElBoq ? 'Τύπος' : T.tenderFormula)}</em>`
              : '€' + it.total_eur.toFixed(2);
            rowsHtml.push(`<tr>
              <td><code>${escapeHtml(it.item_no)}</code></td>
              <td>${escapeHtml(it.category)}</td>
              <td>${escapeHtml(txt)}</td>
              <td class="c">${escapeHtml(it.unit)}</td>
              <td class="r">${it.quantity.toFixed(2)}</td>
              <td class="r">${rateCell}</td>
              <td class="r">${totCell}</td>
            </tr>`);
          }
          if (!blank) {
            const subLbl = isElBoq ? 'Μερικό Σύνολο (ΕΝΟΤΗΤΑ' : T.secSubtotal;
            rowsHtml.push(`<tr class="boq-sub-row">
              <td colspan="6" class="r">${escapeHtml(subLbl)} ${code})</td>
              <td class="r">€${secSum.toFixed(2)}</td>
            </tr>`);
          }
        }
        const totals = blank
          ? `<tr><td colspan="7" class="r"><strong>${T.boqTender}
              (${vatPct}% ${T.boqVat})</strong></td></tr>`
          : `<tr><td colspan="6" class="r"><strong>${T.boqSub}</strong></td>
            <td class="r"><strong>€${boq.subtotal_eur.toFixed(2)}</strong></td></tr>
          <tr><td colspan="6" class="r"><strong>${T.boqVat} (${vatPct}%)</strong></td>
            <td class="r"><strong>€${boq.vat_eur.toFixed(2)}</strong></td></tr>
          <tr><td colspan="6" class="r"><strong>${T.boqGrand}</strong></td>
            <td class="r"><strong>€${boq.grand_total_eur.toFixed(2)}</strong></td></tr>`;
        document.getElementById('boqBody').innerHTML = rowsHtml.join('') + totals;
      }

      const rank = { blocker: 0, warning: 1, info: 2 };
      const sorted = [...d.review_flags].sort((a,b) => rank[a.severity] - rank[b.severity]);
      document.getElementById('flagsBody').innerHTML = sorted.map(f => {
        const cls = f.severity === 'blocker'
          ? 'b-fail'
          : (f.severity === 'warning' ? 'b-warn' : 'b-info');
        const sev = escapeHtml(f.severity).toUpperCase();
        return `<tr>
          <td class="c"><span class="badge ${cls}">${sev}</span></td>
          <td><code>${escapeHtml(f.code)}</code></td>
          <td>${escapeHtml(f.category)}</td>
          <td class="c">${escapeHtml(f.circuit_id || '-')}</td>
          <td>${escapeHtml(f.regulation_ref || '-')}</td>
          <td>${escapeHtml(f.message)}</td>
        </tr>`;
      }).join('');
    }

    // Delegated listener for Pass-2 circuit table edits
    document.getElementById('circuitsBody').addEventListener('change', (ev) => {
      const el = ev.target;
      if (el && el.dataset && el.dataset.cid && el.dataset.field) {
        setCircuitOverride(el.dataset.cid, el.dataset.field, el.value);
      }
    });

    // Delegated click listener for Interactive Visual Floorplan (Room Reclass & Anchor Relocation)
    document.getElementById('floorplanSvg').addEventListener('click', (ev) => {
      const svg = document.querySelector('#floorplanSvg svg');
      if (!svg) return;
      if (dbRelocateMode) {
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
        const target = (document.getElementById('anchorTargetSel') || { value: 'db' }).value;
        const ptMm = { x_mm: xMm, y_mm: yMm };
        if (target === 'meter') meterLocationOverride = ptMm;
        else if (target === 'earth_pit') earthPitLocationOverride = ptMm;
        else if (target === 'telecom') telecomLocationOverride = ptMm;
        else dbLocationOverride = ptMm;
        toggleDbMode();
        runSolve();
        return;
      }
      const poly = ev.target.closest('polygon[data-room-id]');
      if (!poly) return;
      selectedRoomId = poly.dataset.roomId;
      const curType = poly.dataset.roomType || 'storage';
      const lbl = poly.dataset.roomLabel || selectedRoomId;
      document.getElementById('roomEditLbl').textContent = `${selectedRoomId} (${lbl}):`;
      const sel = document.getElementById('roomTypeSel');
      sel.innerHTML = ROOM_TYPES.map(rt =>
        `<option value="${rt}" ${rt === curType ? 'selected' : ''}>${rt}</option>`
      ).join('');
      document.getElementById('roomEditBox').style.display = 'inline-flex';
    });

    // Delegated click listener for Team Seat Reset Password (clean, XSS-safe, no inline onclick)
    document.getElementById('teamBox').addEventListener('click', (ev) => {
      const btn = ev.target.closest('button[data-reset-uid]');
      if (!btn) return;
      resetMemberPassword(btn.dataset.resetUid, btn.dataset.resetEmail || '');
    });

    // Keyboard shortcuts: Escape closes Fullscreen SVG modal; Enter submits Auth Modal
    document.addEventListener('keydown', (ev) => {
      if (ev.key === 'Escape') {
        closeFullscreenSvg();
      }
    });
    document.getElementById('authModal').addEventListener('keydown', (ev) => {
      if (ev.key === 'Enter' && ev.target.tagName === 'INPUT') {
        ev.preventDefault();
        submitAuthModal();
      }
    });

    function authHeaders(isJson) {
      const h = { 'X-Tenant-ID': document.getElementById('tenantId').value || 'anonymous' };
      let jwt = '';
      try { jwt = (sessionStorage.getItem('cyelec_jwt') || '').trim(); } catch (_) {}
      if (jwt) h['Authorization'] = `Bearer ${jwt}`;
      const key = (document.getElementById('apiKeyInput').value || '').trim();
      if (key) h['X-API-Key'] = key;
      if (isJson) h['Content-Type'] = 'application/json';
      return h;
    }

    document.getElementById('apiKeyInput').addEventListener('change', (ev) => {
      try { sessionStorage.setItem('cyelec_api_key', ev.target.value.trim()); } catch (_) {}
    });

    // Switching benchmark clears stale overrides & uploaded file, then solves automatically
    document.getElementById('benchmarkSel').addEventListener('change', () => {
      const fi = document.getElementById('dxfFile');
      if (fi && fi.files.length > 0) {
        clearUploadedFiles();
        return;
      }
      clearDesignOverrides();
      runSolve();
    });

    // Multi-Floor selector + DXF Layer Mapping Wizard trigger on file select
    document.getElementById('dxfFile').addEventListener('change', async (ev) => {
      const files = ev.target.files;
      const mfBox = document.getElementById('multiFloorBox');
      const mfRows = document.getElementById('multiFloorRows');
      const box = document.getElementById('layerWizard');
      const rows = document.getElementById('layerRows');
      const fRow = document.getElementById('fileStatusRow');
      const fTxt = document.getElementById('fileStatusTxt');
      Object.keys(layerRoleChoices).forEach(k => delete layerRoleChoices[k]);
      clearDesignOverrides();

      if (files.length > 0) {
        const names = Array.from(files).map(x => x.name).join(', ');
        fTxt.textContent = files.length === 1 ? names : `${files.length} files: ${names}`;
        fRow.style.display = 'flex';
      } else {
        fRow.style.display = 'none';
        fTxt.textContent = '';
      }

      if (files.length > 1) {
        mfRows.innerHTML = Array.from(files).map((fl, i) => {
          const defLbl = (i === 0) ? 'GF' : `${i}F`;
          const defLen = (i === 0) ? 8 : (8 + i * 6);
          return `<div class="layer-row" style="grid-template-columns:1fr 56px 56px;">
            <span title="${escapeHtml(fl.name)}">${escapeHtml(fl.name)}</span>
            <input data-mf-lbl="${i}" value="${defLbl}" title="Floor Label" />
            <input data-mf-len="${i}" type="number" min="1" value="${defLen}"
              title="Sub-main m" />
          </div>`;
        }).join('');
        mfBox.style.display = 'block';
      } else {
        mfBox.style.display = 'none';
        mfRows.innerHTML = '';
      }

      const f = files[0];
      const fnLow = f ? f.name.toLowerCase() : '';
      if (!f || (!fnLow.endsWith('.dxf') && !fnLow.endsWith('.dwg'))) {
        box.style.display = 'none';
        return;
      }
      const fd = new FormData();
      fd.append('file', f);
      try {
        const resp = await fetch('/v1/dxf/layers', {
          method: 'POST',
          headers: authHeaders(false),
          body: fd
        });
        if (!resp.ok) return;
        const body = await resp.json();
        rows.innerHTML = body.layers.map(l => {
          const lname = escapeHtml(l.layer_name);
          const opts = LAYER_ROLES.map(r => {
            const sel = (r === 'auto') ? 'selected' : '';
            const lbl = (r === 'auto') ? `auto (${l.inferred_role})` : r;
            return `<option value="${r}" ${sel}>${escapeHtml(lbl)}</option>`;
          }).join('');
          return `<div class="layer-row">
            <span title="${lname}">${lname} (${l.entity_count})</span>
            <select data-layer="${lname}">${opts}</select>
          </div>`;
        }).join('');
        box.style.display = 'block';
      } catch (_) {
        box.style.display = 'none';
      }
    });

    document.getElementById('layerRows').addEventListener('change', (ev) => {
      const el = ev.target;
      if (el && el.dataset && el.dataset.layer) {
        layerRoleChoices[el.dataset.layer] = el.value;
      }
    });

    async function saveFirmDefaults() {
      const ov = collectOverrides();
      const vat = parseFloat(document.getElementById('vatRateSel').value || '0.19');
      const payload = {
        report_language: ov.report_language,
        branding: ov.branding || null,
        boq_rate_overrides: { vat_rate: vat }
      };
      try {
        const resp = await fetch('/v1/tenants/me', {
          method: 'PUT',
          headers: authHeaders(true),
          body: JSON.stringify(payload)
        });
        if (!resp.ok) throw new Error(await resp.text());
        setStatus(T.savedDefaultsOk, 'ok');
      } catch (err) {
        setStatus(`${T.saveDefaultsFail}: ${err.message}`, 'err');
      }
    }

    async function runSolve() {
      if (isSolving) return;
      isSolving = true;
      const runBtn = document.getElementById('btnRunSolve');
      const zipBtn = document.getElementById('btnDownloadZip');
      if (runBtn) { runBtn.disabled = true; runBtn.textContent = T.solvingBtn; }
      if (zipBtn) zipBtn.disabled = true;
      setStatus(T.solving, '');
      const fileInput = document.getElementById('dxfFile');
      const overrides = collectOverrides();

      try {
        let resp;
        if (fileInput.files.length > 1) {
          const fd = new FormData();
          for (const fl of fileInput.files) fd.append('files', fl);
          const meta = collectMultiFloorMeta(fileInput.files);
          fd.append('floors_json', JSON.stringify(meta.labels));
          if (Object.keys(meta.submains).length > 0) {
            fd.append('submain_lengths_json', JSON.stringify(meta.submains));
          }
          fd.append('overrides_json', JSON.stringify(overrides));
          const lm = collectLayerMapping();
          if (lm) fd.append('layer_mapping_json', JSON.stringify(lm));
          resp = await fetch('/v1/solve/dxf/multifloor', {
            method: 'POST',
            headers: authHeaders(false),
            body: fd
          });
        } else if (fileInput.files.length === 1) {
          const fd = new FormData();
          fd.append('file', fileInput.files[0]);
          fd.append('overrides_json', JSON.stringify(overrides));
          const lm = collectLayerMapping();
          if (lm) fd.append('layer_mapping_json', JSON.stringify(lm));
          resp = await fetch('/v1/solve/dxf/preview', {
            method: 'POST',
            headers: authHeaders(false),
            body: fd
          });
        } else {
          const tmpl = document.getElementById('benchmarkSel').value;
          resp = await fetch(`/v1/solve/benchmark/${tmpl}`, {
            method: 'POST',
            headers: authHeaders(true),
            body: JSON.stringify(overrides)
          });
        }
        const preAlert = document.getElementById('cadPreflightAlert');
        if (!resp.ok) {
          let errTxt = await resp.text();
          try { errTxt = JSON.parse(errTxt).detail || errTxt; } catch (_) {}
          if (resp.status === 422 && preAlert) {
            preAlert.style.display = 'block';
            preAlert.innerHTML =
              `<strong>CAD Preflight Rejected (HTTP 422):</strong> ${escapeHtml(errTxt)}` +
              `<br/><strong>How to fix:</strong> Export a native layered AutoCAD/ArchiCAD/Revit ` +
              `<code>.dxf</code>/<code>.dwg</code>/<code>.ifc</code> with readable ` +
              `<code>TEXT/MTEXT</code> room labels, or assign custom wall/room layers in the ` +
              `<strong>CAD Layer Mapping Wizard</strong> below.`;
          }
          throw new Error(errTxt);
        }
        if (preAlert) { preAlert.style.display = 'none'; preAlert.innerHTML = ''; }
        const data = await resp.json();
        renderResult(data);
        const ms = resp.headers.get('X-Process-Time-Ms') || '?';
        const sha = data.result.design.provenance.input_sha256.slice(0, 12);
        setStatus(`${T.solvedCleanly} ${T.solvedIn} ${ms} ms (SHA-256: ${sha}).`, 'ok');
      } catch (err) {
        setStatus(`${T.solveFailed}: ${err.message}`, 'err');
      } finally {
        isSolving = false;
        if (runBtn) { runBtn.disabled = false; runBtn.textContent = T.runSolveBtn; }
        if (zipBtn) zipBtn.disabled = false;
      }
    }

    async function downloadBundle() {
      if (isDownloading) return;
      isDownloading = true;
      const zipBtn = document.getElementById('btnDownloadZip');
      if (zipBtn) { zipBtn.disabled = true; zipBtn.textContent = T.downloadingZip; }
      setStatus(T.downloadingStatus, '');
      const fileInput = document.getElementById('dxfFile');
      const overrides = collectOverrides();
      try {
        let resp;
        if (fileInput.files.length > 1) {
          const fd = new FormData();
          for (const fl of fileInput.files) fd.append('files', fl);
          const meta = collectMultiFloorMeta(fileInput.files);
          fd.append('floors_json', JSON.stringify(meta.labels));
          if (Object.keys(meta.submains).length > 0) {
            fd.append('submain_lengths_json', JSON.stringify(meta.submains));
          }
          fd.append('overrides_json', JSON.stringify(overrides));
          const lm = collectLayerMapping();
          if (lm) fd.append('layer_mapping_json', JSON.stringify(lm));
          resp = await fetch('/v1/solve/dxf/multifloor/bundle', {
            method: 'POST',
            headers: authHeaders(false),
            body: fd
          });
        } else if (fileInput.files.length === 1) {
          const fd = new FormData();
          fd.append('file', fileInput.files[0]);
          fd.append('overrides_json', JSON.stringify(overrides));
          const lm = collectLayerMapping();
          if (lm) fd.append('layer_mapping_json', JSON.stringify(lm));
          resp = await fetch('/v1/solve/dxf/bundle', {
            method: 'POST',
            headers: authHeaders(false),
            body: fd
          });
        } else {
          const tmpl = document.getElementById('benchmarkSel').value;
          resp = await fetch(`/v1/solve/benchmark/${tmpl}/bundle`, {
            method: 'POST',
            headers: authHeaders(true),
            body: JSON.stringify(overrides)
          });
        }
        if (!resp.ok) {
          let errTxt = await resp.text();
          try { errTxt = JSON.parse(errTxt).detail || errTxt; } catch (_) {}
          throw new Error(errTxt);
        }
        const blob = await resp.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = 'eac_submission_pack.zip';
        a.click();
        URL.revokeObjectURL(url);
        setStatus(T.downloadedZipOk, 'ok');
      } catch (err) {
        setStatus(`${T.downloadFailed}: ${err.message}`, 'err');
      } finally {
        isDownloading = false;
        if (zipBtn) { zipBtn.disabled = false; zipBtn.textContent = T.downloadZipBtn; }
      }
    }

    async function loadProjects() {
      const resp = await fetch('/v1/projects', { headers: authHeaders(false) });
      if (!resp.ok) return;
      const list = await resp.json();
      const sel = document.getElementById('projectSel');
      const cur = sel.value;
      if (!list.length) {
        sel.innerHTML = `<option value="">${escapeHtml(T.noProjects)}</option>`;
        return;
      }
      sel.innerHTML = list.map(p =>
        `<option value="${escapeHtml(p.project_id)}">`
        + `${escapeHtml(p.name)} (${p.revision_count} ${T.revLabel})</option>`
      ).join('');
      if (cur && list.some(p => p.project_id === cur)) sel.value = cur;
      if (sel.value) loadRevisions();
    }

    async function createAndSaveRevision() {
      const st = document.getElementById('wsStatus');
      const sel = document.getElementById('projectSel');
      let pid = sel.value;
      const newName = document.getElementById('projName').value.trim();
      if (!pid || newName) {
        const pr = await fetch('/v1/projects', {
          method: 'POST',
          headers: authHeaders(true),
          body: JSON.stringify({ name: newName || 'Cyprus Project' })
        });
        if (!pr.ok) { st.textContent = 'Project creation failed.'; return; }
        const created = await pr.json();
        pid = created.project_id;
        document.getElementById('projName').value = '';
      }
      const fileInput = document.getElementById('dxfFile');
      let resp;
      if (fileInput.files.length === 1) {
        const fd = new FormData();
        fd.append('file', fileInput.files[0]);
        fd.append('overrides_json', JSON.stringify(collectOverrides()));
        const lm = collectLayerMapping();
        if (lm) fd.append('layer_mapping_json', JSON.stringify(lm));
        fd.append('carry_forward', 'true');
        resp = await fetch(`/v1/projects/${encodeURIComponent(pid)}/revisions/dxf`, {
          method: 'POST',
          headers: authHeaders(false),
          body: fd
        });
      } else {
        const tmpl = document.getElementById('benchmarkSel').value;
        resp = await fetch(`/v1/projects/${encodeURIComponent(pid)}/revisions`, {
          method: 'POST',
          headers: authHeaders(true),
          body: JSON.stringify({
            benchmark_template: tmpl,
            overrides: collectOverrides(),
            carry_forward_overrides: true
          })
        });
      }
      if (!resp.ok) { st.textContent = 'Save revision failed.'; return; }
      const rec = await resp.json();
      st.textContent = `Saved Rev ${rec.summary.revision_number} cleanly.`;
      await loadProjects();
      sel.value = pid;
      await loadRevisions();
    }

    async function exportTenantGdpr() {
      const st = document.getElementById('wsStatus');
      const resp = await fetch('/v1/tenants/me/export', { headers: authHeaders(false) });
      if (!resp.ok) { st.textContent = 'GDPR export failed: ' + await resp.text(); return; }
      const blob = await resp.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = 'cyelec_gdpr_export.json';
      a.click();
      URL.revokeObjectURL(url);
      st.textContent = 'Downloaded GDPR Art. 20 portable JSON export.';
    }

    async function eraseTenantGdpr() {
      const st = document.getElementById('wsStatus');
      const msg = 'Permanently erase ALL projects, revisions & usage (GDPR Art. 17)?';
      if (!confirm(msg)) {
        return;
      }
      const resp = await fetch('/v1/tenants/me', {
        method: 'DELETE',
        headers: authHeaders(false)
      });
      if (!resp.ok) { st.textContent = 'GDPR erasure failed: ' + await resp.text(); return; }
      st.textContent = 'Tenant workspace permanently erased (GDPR Art. 17).';
      document.getElementById('revListBox').innerHTML = '';
      document.getElementById('revDiffBox').innerHTML = '';
      await loadProjects();
    }

    async function loadRevisions() {
      const pid = document.getElementById('projectSel').value;
      if (!pid) return;
      const resp = await fetch(`/v1/projects/${encodeURIComponent(pid)}/revisions`, {
        headers: authHeaders(false)
      });
      if (!resp.ok) return;
      const revs = await resp.json();
      const rows = revs.map(r => `<tr>
        <td><strong>Rev ${r.revision_number}</strong></td>
        <td><code>${escapeHtml(r.input_sha256.slice(0, 12))}</code></td>
        <td>${escapeHtml(r.supply_tier)}</td>
        <td class="r">${r.diversified_kva.toFixed(2)} kVA</td>
        <td class="r">€${r.boq_grand_total_eur.toFixed(2)}</td>
        <td>${escapeHtml(r.created_at)}</td>
      </tr>`).join('');
      document.getElementById('revListBox').innerHTML = `<div class="tbl-wrap"><table>
        <thead><tr>
          <th>${T.thRev}</th><th>Input SHA</th><th>${T.thSupplyTier}</th>
          <th class="r">Div. kVA</th><th class="r">BOQ (EUR)</th><th>${T.thTimestamp}</th>
        </tr></thead><tbody>${rows}</tbody></table></div>`;
    }

    async function diffLatestRevisions() {
      const pid = document.getElementById('projectSel').value;
      const box = document.getElementById('revDiffBox');
      if (!pid) return;
      const rResp = await fetch(`/v1/projects/${encodeURIComponent(pid)}/revisions`, {
        headers: authHeaders(false)
      });
      const revs = await rResp.json();
      if (revs.length < 2) {
        box.textContent = T.need2Revs;
        return;
      }
      const a = revs[revs.length - 2].revision_number;
      const b = revs[revs.length - 1].revision_number;
      const dResp = await fetch(
        `/v1/projects/${encodeURIComponent(pid)}/diff/${a}/${b}`,
        { headers: authHeaders(false) }
      );
      if (!dResp.ok) return;
      const diff = await dResp.json();
      const cRows = diff.circuit_deltas.map(c => `<tr>
        <td><code>${escapeHtml(c.circuit_id)}</code></td>
        <td>${escapeHtml(c.change)}</td>
        <td>${escapeHtml(c.old_summary || '-')}</td>
        <td>${escapeHtml(c.new_summary || '-')}</td>
      </tr>`).join('');
      box.innerHTML = `<p><strong>Diff Rev ${a} -&gt; Rev ${b}:</strong>
        ΔkVA=${diff.diversified_kva_delta} | ΔConduit=${diff.conduit_m_delta} m |
        ΔBOQ=€${diff.boq_eur_delta.toFixed(2)}</p>
        <div class="tbl-wrap"><table><thead><tr>
          <th>Circuit</th><th>Change</th><th>Rev ${a}</th><th>Rev ${b}</th>
        </tr></thead><tbody>${cRows || '<tr><td colspan="4">No circuit deltas.</td></tr>'}
        </tbody></table></div>`;
    }

    const AUTH_REQUIRED = __AUTH_REQ_JS__;
    const INVITE_CODE_REQUIRED = __INVITE_REQ_JS__;
    let authModalMode = 'login';

    function openAuthModal(mode) {
      authModalMode = mode || 'login';
      switchAuthModalTab(authModalMode);
      document.getElementById('mError').textContent = '';
      document.getElementById('authModal').classList.add('open');
    }

    function closeAuthModal() {
      document.getElementById('authModal').classList.remove('open');
      let hasJwt = false;
      try {
        hasJwt = Boolean((sessionStorage.getItem('cyelec_jwt') || '').trim());
      } catch (_) {}
      const hasKey = Boolean((document.getElementById('apiKeyInput').value || '').trim());
      const banner = document.getElementById('authGateBanner');
      if (banner) {
        banner.style.display = (AUTH_REQUIRED && !hasJwt && !hasKey) ? 'flex' : 'none';
      }
    }

    function switchAuthModalTab(mode) {
      authModalMode = mode;
      const isReg = (mode === 'register');
      document.getElementById('mTabLogin').classList.toggle('active', !isReg);
      document.getElementById('mTabReg').classList.toggle('active', isReg);
      document.getElementById('modalRegFields').style.display = isReg ? 'block' : 'none';
      document.getElementById('mSubmitBtn').textContent = isReg
        ? T.createFirmBtn
        : T.signInBtn;
      document.getElementById('mError').textContent = '';
    }

    function applySignedInSession(data) {
      if (data.access_token) {
        try { sessionStorage.setItem('cyelec_jwt', data.access_token); } catch (_) {}
      }
      const t = data.tenant || {};
      const u = data.user || data.principal || {};
      const q = data.quota || null;
      if (t.tenant_id) document.getElementById('tenantId').value = t.tenant_id;
      if (t.branding) {
        if (t.branding.firm_name) document.getElementById('firmName').value = t.branding.firm_name;
        if (t.branding.engineer_name) {
          document.getElementById('engName').value = t.branding.engineer_name;
        }
        if (t.branding.etek_reg_no) {
          document.getElementById('etekReg').value = t.branding.etek_reg_no;
        }
      }
      if (t.report_language) document.getElementById('reportLang').value = t.report_language;
      if (t.boq_rate_overrides && t.boq_rate_overrides.vat_rate != null) {
        const vSel = document.getElementById('vatRateSel');
        const vStr = Number(t.boq_rate_overrides.vat_rate).toFixed(2);
        if (vSel && Array.from(vSel.options).some(o => o.value === vStr)) vSel.value = vStr;
      }
      const roleStr = (u.role || 'owner').toUpperCase();
      const emailStr = u.email || t.display_name || t.tenant_id || 'Signed In';
      const qStr = q ? ` · ${q.solves_used}/${q.solves_limit} solves` : '';
      document.getElementById('authBadge').textContent =
        `Signed in: ${emailStr} (${roleStr})${qStr}`;
      document.getElementById('hdrAccountBtn').textContent =
        `${emailStr} (${roleStr})${qStr}`;
      const banner = document.getElementById('authGateBanner');
      if (banner) banner.style.display = 'none';
      closeAuthModal();
    }

    async function submitAuthModal() {
      const errEl = document.getElementById('mError');
      errEl.textContent = '';
      const email = document.getElementById('mEmail').value.trim();
      const password = document.getElementById('mPass').value;
      const isReg = (authModalMode === 'register');
      const endpoint = isReg ? '/v1/auth/register' : '/v1/auth/login';
      const body = isReg
        ? {
            firm_name: document.getElementById('mFirm').value.trim(),
            display_name: document.getElementById('mName').value.trim(),
            invite_code: (document.getElementById('mInvite').value || '').trim(),
            email,
            password
          }
        : { email, password };
      try {
        const resp = await fetch(endpoint, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body)
        });
        if (!resp.ok) {
          let msg = await resp.text();
          try { msg = JSON.parse(msg).detail || msg; } catch (_) {}
          errEl.textContent = msg;
          return;
        }
        const data = await resp.json();
        applySignedInSession(data);
        await loadSubscriptionAndTeam();
        await runSolve();
      } catch (err) {
        errEl.textContent = err.message || 'Network error';
      }
    }

    async function firmRegister() {
      const st = document.getElementById('wsStatus');
      const body = {
        firm_name: document.getElementById('authFirm').value.trim(),
        display_name: (document.getElementById('authDisplayName').value || '').trim(),
        invite_code: (document.getElementById('authInvite').value || '').trim(),
        email: document.getElementById('authEmail').value.trim(),
        password: document.getElementById('authPass').value
      };
      const resp = await fetch('/v1/auth/register', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
      });
      if (!resp.ok) { st.textContent = `Register failed: ${await resp.text()}`; return; }
      const data = await resp.json();
      applySignedInSession(data);
      st.textContent = `Registered firm ${data.tenant.display_name}.`;
      await loadSubscriptionAndTeam();
      await runSolve();
    }

    async function firmLogin() {
      const st = document.getElementById('wsStatus');
      const body = {
        email: document.getElementById('authEmail').value.trim(),
        password: document.getElementById('authPass').value
      };
      const resp = await fetch('/v1/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
      });
      if (!resp.ok) { st.textContent = `Sign in failed: ${await resp.text()}`; return; }
      const data = await resp.json();
      applySignedInSession(data);
      st.textContent = `Welcome back, ${data.user.display_name}.`;
      await loadSubscriptionAndTeam();
      await runSolve();
    }

    function firmLogout() {
      try { sessionStorage.removeItem('cyelec_jwt'); } catch (_) {}
      document.getElementById('authBadge').textContent = T.signedOut;
      document.getElementById('hdrAccountBtn').textContent = T.signInRegBtn;
      document.getElementById('wsStatus').textContent = T.signedOut;
      if (AUTH_REQUIRED) openAuthModal('login');
    }

    async function changeMyPassword() {
      const st = document.getElementById('wsStatus');
      const cur = document.getElementById('chgCurPass').value;
      const nxt = document.getElementById('chgNewPass').value;
      const resp = await fetch('/v1/auth/change-password', {
        method: 'POST',
        headers: authHeaders(true),
        body: JSON.stringify({ current_password: cur, new_password: nxt })
      });
      if (!resp.ok) { st.textContent = `Password change failed: ${await resp.text()}`; return; }
      document.getElementById('chgCurPass').value = '';
      document.getElementById('chgNewPass').value = '';
      st.textContent = 'Your password has been updated.';
    }

    async function resetMemberPassword(uid, email) {
      const st = document.getElementById('wsStatus');
      const npw = prompt(`Enter new temporary password (8+ chars) for ${email}:`);
      if (!npw || npw.length < 8) return;
      const resp = await fetch(`/v1/team/members/${encodeURIComponent(uid)}/reset-password`, {
        method: 'POST',
        headers: authHeaders(true),
        body: JSON.stringify({ new_password: npw })
      });
      if (!resp.ok) { st.textContent = `Reset failed: ${await resp.text()}`; return; }
      st.textContent = `Password reset for ${email}.`;
    }

    async function loadSubscriptionAndTeam() {
      const subBox = document.getElementById('subBox');
      const teamBox = document.getElementById('teamBox');
      const sResp = await fetch('/v1/billing/subscription', { headers: authHeaders(false) });
      if (sResp.ok) {
        const s = await sResp.json();
        const q = s.quota;
        subBox.innerHTML = `<strong>${escapeHtml(s.plan.display_name)}</strong>
          (<code>${escapeHtml(s.plan.plan_id)}</code> · ${escapeHtml(s.subscription_status)})
          <br/>Monthly Solves (${escapeHtml(q.month_key)}):
          <strong>${q.solves_used} / ${q.solves_limit}</strong>
          &nbsp;|&nbsp; Seats: <strong>${s.seats_used} / ${s.seats_limit}</strong>`;
      }
      const tResp = await fetch('/v1/team/members', { headers: authHeaders(false) });
      if (tResp.ok) {
        const members = await tResp.json();
        teamBox.innerHTML = members.length
          ? members.map(m =>
              `<div style="display:flex;justify-content:space-between;align-items:center;
                           gap:6px;margin:3px 0;">
                <span><code>${escapeHtml(m.role)}</code> — ${escapeHtml(m.email)}
                (${escapeHtml(m.display_name)})</span>
                <button class="btn btn-secondary btn-xs"
                  data-reset-uid="${escapeHtml(m.user_id)}"
                  data-reset-email="${escapeHtml(m.email)}">Reset PW</button>
              </div>`
            ).join('')
          : T.noSeatsYet;
      }
    }

    async function addTeamMember() {
      const st = document.getElementById('wsStatus');
      const body = {
        email: document.getElementById('memEmail').value.trim(),
        display_name: document.getElementById('memName').value.trim(),
        password: document.getElementById('memPass').value,
        role: document.getElementById('memRole').value
      };
      const resp = await fetch('/v1/team/members', {
        method: 'POST',
        headers: authHeaders(true),
        body: JSON.stringify(body)
      });
      if (!resp.ok) { st.textContent = `Add seat failed: ${await resp.text()}`; return; }
      st.textContent = `Added ${body.role} seat for ${body.email}.`;
      await loadSubscriptionAndTeam();
    }

    async function startCheckout(planId) {
      const st = document.getElementById('wsStatus');
      const resp = await fetch('/v1/billing/checkout', {
        method: 'POST',
        headers: authHeaders(true),
        body: JSON.stringify({ plan_id: planId })
      });
      if (!resp.ok) { st.textContent = `Checkout unavailable: ${await resp.text()}`; return; }
      const data = await resp.json();
      if (data.checkout_url) window.open(data.checkout_url, '_blank', 'noopener');
    }

    async function openBillingPortal() {
      const st = document.getElementById('wsStatus');
      const resp = await fetch('/v1/billing/portal', {
        method: 'POST',
        headers: authHeaders(false)
      });
      if (!resp.ok) { st.textContent = `Portal unavailable: ${await resp.text()}`; return; }
      const data = await resp.json();
      if (data.portal_url) window.open(data.portal_url, '_blank', 'noopener');
    }

    const CONFIG_SELECT_IDS = [
      'eacProfile', 'earthing', 'supplyHeadroomSel', 'reportLang', 'vatRateSel',
      'briefHvac', 'briefEvKw', 'briefConduitStyle', 'briefEarthing',
      'eacDistrict', 'eacSupplyConn'
    ];
    for (const sid of CONFIG_SELECT_IDS) {
      const selEl = document.getElementById(sid);
      if (selEl) {
        selEl.addEventListener('change', () => {
          const runBtn = document.getElementById('btnRunSolve');
          if (runBtn && !isSolving) runBtn.textContent = T.runSolveDirty;
          setStatus(T.settingsChanged, '');
        });
      }
    }

    window.addEventListener('DOMContentLoaded', async () => {
      let savedJwt = '';
      let savedKey = '';
      try {
        savedKey = (sessionStorage.getItem('cyelec_api_key') || '').trim();
        if (savedKey) document.getElementById('apiKeyInput').value = savedKey;
        savedJwt = (sessionStorage.getItem('cyelec_jwt') || '').trim();
      } catch (_) {}

      if (savedJwt) {
        const meResp = await fetch('/v1/auth/me', { headers: authHeaders(false) });
        if (meResp.ok) {
          const me = await meResp.json();
          applySignedInSession(me);
          await loadSubscriptionAndTeam();
          await runSolve();
          return;
        }
        try { sessionStorage.removeItem('cyelec_jwt'); } catch (_) {}
      }

      if (AUTH_REQUIRED && !savedKey) {
        setStatus(T.authSignInPrompt, '');
        openAuthModal('login');
        return;
      }
      await runSolve();
    });
