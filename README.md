# cyelec-pipeline

Deterministic-first, zero-commercial-CAD-license Python 3.12 electrical engineering pipeline targeting **BS 7671**, **IEC 60364**, and **Electricity Authority of Cyprus (EAC)** plan-approval deliverables, architected for EU **Multi-Tenant B2B SaaS** deployment.

## Architecture Principles

- **Zero commercial CAD licenses & zero AGPLv3 SaaS traps:** Uses only MIT/BSD/LGPL Python packages (`ezdxf`, `shapely`, `networkx`, `pydantic`, `scipy`, `openpyxl`, `pyyaml`, `typer`). Never uses `PyMuPDF` (`fitz`).
- **100% deterministic engineering core:** All cable sizing, derating ($C_a, C_g, C_i$), voltage drop, $Z_s$ / TT RCD touch-voltage verification, adiabatic CPC sizing, EAC supply tiering, and exact MILP 3-phase balancing are backed by versioned YAML standards tables in `standards/`.
- **Stateless SaaS & ETEK governance:** Every pipeline execution accepts structured engineer `DesignOverrides`, emits SHA-256 + standards-version provenance, and produces an explicit `ReviewFlag` (`info` / `warning` / `blocker`) register gating statutory **ETEK Electrical Engineer** review and sign-off.

## Standards Tables (`standards/`)

| File | Scope |
| --- | --- |
| `standards/bs7671_cables.yaml` | Appendix 4 Method B (Cyprus masonry conduit default) & Method C ratings (`1.5–35 mm²`), `mV/A/m`, 20°C resistance, $C_a$ (Table 4B1), $C_g$ (Table 4C1), $C_i$ (Table 52.2), temp multiplier `1.20`, adiabatic $k=115$ |
| `standards/bs7671_devices.yaml` | BS 7671 Table 41.3 Type B & C MCB/RCBO $Z_{s,\max}$ ($U_0=230\text{ V}, C_{\min}=0.95$) and let-through $I^2t$ (`6–63 A`) |
| `standards/eac_cyprus_rules.yaml` | Dual profiles (`cyprus_eac_17th_a3`: `3.0 m` / `cyprus_eac_18th_a2`: `2.5 m` bathroom exclusion), Cyprus ambient (`40°C` indoor / `45°C` outdoor), TT earthing default ($R_A \cdot I_{\Delta n} \le 50\text{ V}$, `200 Ω` stability warning), 3%/5% $\Delta V$ limits, EAC 1P/3P, PV `> 5 kVA`, CT metering (`> 100 A/phase`), substation thresholds (`100 / 150 kVA`) |
| `standards/room_templates.yaml` | Bilingual English + Greek aliases (`ΣΑΛΟΝΙ`, `ΚΟΥΖΙΝΑ`, `ΥΠΝΟΔΩΜΑΤΙΟ`, `ΜΠΑΝΙΟ`, `ΔΙΑΔΡΟΜΟΣ`, `ΠΛΥΣΤΑΡΙΟ`, `ΒΕΡΑΝΤΑ`, etc.) and Cyprus domestic circuit load templates |

## Quickstart

```bash
pip install -e ".[dev]"

# Verify versioned standards tables
cyelec check-standards

# Generate 120 m² synthetic Cyprus 3-bedroom apartment R2018 DXF
cyelec synth-plan --out out/cyprus_3bed/sample_plan.dxf

# Run end-to-end Phase 1 pipeline (DXF + Excel + CSV + MD + A4-landscape HTML + JSON)
cyelec solve-synth --out-dir out/cyprus_3bed

# Phase 2: Inspect any architectural DXF (EN/EL rooms, doors, windows, fixtures, DB1)
cyelec inspect-dxf out/cyprus_3bed/cyprus_3bed_120m2.dxf

# Phase 2: End-to-end DXF -> Section 701 wet zones -> valid-wall placement -> NetworkX routing -> BS 7671/EAC deliverables + electrical_layout.dxf
cyelec solve-dxf out/cyprus_3bed/cyprus_3bed_120m2.dxf --out-dir out/cyprus_3bed_phase2
```

## Development Checks

```bash
ruff check src tests
mypy src
pytest -v
```
