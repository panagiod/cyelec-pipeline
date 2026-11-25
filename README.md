# cyelec-pipeline

Deterministic-first, zero-commercial-CAD-license Python 3.12 electrical engineering pipeline & EU **Multi-Tenant B2B SaaS** platform targeting **BS 7671**, **IEC 60364**, and **Electricity Authority of Cyprus (EAC)** plan-approval deliverables.

## Architecture Principles

- **Zero commercial CAD licenses & zero AGPLv3 SaaS traps:** Uses only MIT/BSD/LGPL Python packages (`ezdxf`, `shapely`, `networkx`, `pydantic`, `scipy`, `openpyxl`, `pyyaml`, `typer`, optional `ifcopenshell` / `weasyprint`). Never uses `PyMuPDF` (`fitz`). `QElectroTech` (GPLv2+) is isolated strictly as an external subprocess.
- **100% deterministic engineering core:** All spatial geometry, BS 7671 Section 701 wet zones, NetworkX conduit routing, cable sizing, derating ($C_a, C_g, C_i$), voltage drop, $Z_s$ / TT RCD touch-voltage verification, BS 7671 Section 722 EV protection, adiabatic CPC sizing, multi-floor sub-main cumulative $\Delta V$, Cyprus BOQ (EUR + 19% VAT), EAC supply tiering, and exact MILP 3-phase balancing are backed by versioned YAML standards tables in `standards/`.
- **Multi-Tenant EU B2B SaaS & ETEK Governance:** Strict per-tenant workspace isolation, PBKDF2-SHA256 API keys, Pass-2 `DesignOverrides` carry-forward across architectural revisions (`Rev 1` vs `Rev 2` deterministic diffing), tenant firm branding & bilingual EN/EL deliverables, usage metering, SHA-256 provenance, and an explicit `ReviewFlag` (`info` / `warning` / `blocker`) register gating statutory **ETEK Electrical Engineer** review and sign-off.

## Product Capabilities

1. **Messy Real-World DXF Resilience:** Recursive AutoCAD `INSERT` block explosion (Layer `"0"` inheritance & block-name hints), wall-to-room `shapely.ops.polygonize` fallback when `A-ROOM` polylines are missing, Cyprus Greeklish/abbreviated room aliases (`SALONI`, `KOUZ.`, `YPN. 1`, `MPANIO`, `WC/NTOYS`), pre-ingest DXF layer inspection (`POST /v1/dxf/layers`, `cyelec inspect-dxf --layers`), and explicit `DxfLayerMapping`.
2. **Multi-Floor Buildings & Sub-Distribution Boards (`MDB -> DB-GF / DB-1F`):** BS 7671 sub-main feeder sizing at Cyprus 40°C ambient, mandatory TT time-delayed 100 mA Type S selective RCDs (Reg 536.4.1.4), automatic cumulative voltage drop enforcement ($\Delta V_{\text{sub-main}} + \Delta V_{\text{final}} \le 3\%$ lighting / $5\%$ power), hierarchical SLD rendering, and a 2-storey Cyprus villa benchmark (`villa_2storey`).
3. **Cyprus BOQ (EUR + 19% VAT), Tenant Branding, Bilingual EN/EL & Optional Native PDF:** Deterministic EUR Bill of Quantities exported as a 3rd live-formula Excel sheet (`Bill of Quantities`) + `boq.csv`, English / Greek / Bilingual (`EN / EL`) deliverables, consulting firm & ETEK engineer branding across reports/SLD/CAD `E-TITLE`, and optional BSD-licensed WeasyPrint native PDF export (`--pdf`).
4. **Interactive Click-to-Edit Visual Floorplan Workbench:** Zero-CDN, GDPR-safe ETEK review dashboard (`GET /`) featuring click-to-reclassify rooms (`room_type_overrides`), click-to-relocate `DB1` (`db_location_override_mm`) with live NetworkX conduit re-routing, DXF Layer Mapping Wizard, live BOQ tab, and interactive Pass-2 circuit phase/grouping overrides.
5. **Multi-Tenant SaaS Workspace, Revision Diffing & Usage Metering:** Tenant-scoped SQLite repository (`CYELEC_DB_PATH`) with `/v1/tenants/me`, `/v1/tenants/me/api-key`, `/v1/projects`, `/v1/projects/{id}/revisions`, `/v1/projects/{id}/diff/{rev_a}/{rev_b}`, and `/v1/tenant/usage`.

## Standards Tables (`standards/`)

| File | Scope |
| --- | --- |
| `standards/bs7671_cables.yaml` | Appendix 4 Method B (Cyprus masonry conduit default) & Method C ratings (`1.5–35 mm²`), `mV/A/m`, 20°C resistance, $C_a$ (Table 4B1), $C_g$ (Table 4C1), $C_i$ (Table 52.2), 70°C PVC & 90°C XLPE temp multipliers (`1.20` / `1.28`) & adiabatic $k$ (`115` / `143`) |
| `standards/bs7671_devices.yaml` | BS 7671 Table 41.3 Type B & C MCB/RCBO $Z_{s,\max}$ ($U_0=230\text{ V}, C_{\min}=0.95$) and let-through $I^2t$ (`6–63 A`) |
| `standards/eac_cyprus_rules.yaml` | Dual profiles (`cyprus_eac_17th_a3`: `3.0 m` / `cyprus_eac_18th_a2`: `2.5 m` bathroom exclusion), Cyprus ambient (`40°C` indoor / `45°C` outdoor), TT earthing default ($R_A \cdot I_{\Delta n} \le 50\text{ V}$, `200 Ω` stability warning), 3%/5% $\Delta V$ limits, EAC 1P/3P, PV `> 5 kVA`, CT metering (`> 100 A/phase`), substation thresholds (`100 / 150 kVA`) |
| `standards/room_templates.yaml` | Bilingual English + Greek + Cyprus Greeklish/abbreviated room aliases and domestic circuit load templates |
| `standards/cyprus_boq_rates.yaml` | Indicative Cyprus electrical contracting supply & install EUR unit rates (cables, PVC conduits, switchgear, BS 1363 accessories, TT earth rod kit, EV chargers, 19% VAT) |

## Quickstart

```bash
pip install -e ".[api,dev]"

# Verify versioned standards tables
cyelec check-standards

# Inspect DXF layers or classified bilingual rooms
cyelec inspect-dxf out/cyprus_3bed/cyprus_3bed_120m2.dxf --layers

# Run complete Cyprus EAC plan-approval pack (bilingual + branded + BOQ + optional PDF)
cyelec solve-eac plan.dxf --out-dir out/eac_pack \
  --language bilingual --firm-name "Kypros MEP Ltd" --engineer "N. Papadopoulos" --etek-reg "ETEK-E-4821"

# Run all 4 Cyprus benchmarks (Apartment, Villa PV+EV, 2-Storey Villa MDB->DB-GF/DB-1F, Commercial Office)
cyelec benchmark-suite --out-dir out/benchmarks

# Launch Multi-Tenant EU B2B SaaS API & Interactive ETEK Review Workbench (http://127.0.0.1:8000/)
cyelec serve --host 0.0.0.0 --port 8000

# Hardened EU SaaS deployment (read-only rootfs, ephemeral tmpfs, persistent tenant DB volume, Caddy TLS)
docker compose -f deploy/docker-compose.yml up --build
```

## Development Checks

```bash
ruff check src tests
mypy src
pytest -v
```
