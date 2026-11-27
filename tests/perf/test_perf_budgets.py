"""Tier-1 Performance Budgets, Memory Ceiling & 48-Room Large-CAD Stress Suite.

Runs on every PR and push in CI (`pytest tests/perf -v`). Enforces hard safety ceilings
while emitting structured JSON telemetry to `CYELEC_PERF_REPORT` (if set) for trend tracking.
"""

from __future__ import annotations

import json
import math
import os
import time
import tracemalloc
from pathlib import Path
from typing import Any

import ezdxf
import pytest

from cyelec.ingest.dxf_ingest import ingest_dxf
from cyelec.models.building import (
    BuildingModel,
    BuildingType,
    DistributionBoardAnchor,
    DoorOpening,
    Point2D,
    Room,
    RoomType,
    SubBoardSpec,
    WindowOpening,
)
from cyelec.models.electrical import DesignOverrides, ResidentialSystemsBrief
from cyelec.pipeline import export_phase3_deliverables, run_phase2_pipeline
from cyelec.synth.benchmarks import BenchmarkTemplate, generate_benchmark_dxf

_PERF_METRICS: dict[str, Any] = {}


def _build_large_48_room_4storey_building() -> BuildingModel:
    """Synthesize a realistic 4-storey, 48-room (720 m²) Cyprus commercial/mixed stress plan."""
    floors = ("GF", "1F", "2F", "3F")
    rooms: list[Room] = []
    doors: list[DoorOpening] = []
    windows: list[WindowOpening] = []
    sub_boards: list[SubBoardSpec] = []
    room_seq = 0
    door_seq = 0
    win_seq = 0

    for f_idx, flr in enumerate(floors):
        x_off = f_idx * 24000.0  # Separate floor clusters side-by-side (24 m spacing)
        sub_boards.append(
            SubBoardSpec(
                board_id=f"DB-{flr}",
                floor=flr,
                location_mm=Point2D(x_mm=x_off + 400.0, y_mm=4500.0),
                submain_length_m=6.0 + f_idx * 4.5,
            )
        )
        # 12 rooms per floor: 1 central corridor + 11 offices/meeting/kitchen/WC/utility rooms
        corridor_poly = [
            Point2D(x_mm=x_off + 0.0, y_mm=4000.0),
            Point2D(x_mm=x_off + 18000.0, y_mm=4000.0),
            Point2D(x_mm=x_off + 18000.0, y_mm=6000.0),
            Point2D(x_mm=x_off + 0.0, y_mm=6000.0),
        ]
        room_seq += 1
        corr_id = f"{flr}-R{room_seq}"
        rooms.append(
            Room(
                id=corr_id,
                raw_label=f"Corridor ({flr})",
                normalized_type=RoomType.CORRIDOR,
                floor=flr,
                polygon_mm=corridor_poly,
                area_m2=36.0,
                ceiling_height_mm=2900.0,
            )
        )
        door_seq += 1
        doors.append(
            DoorOpening(
                id=f"D{door_seq}",
                room_id=corr_id,
                hinge=Point2D(x_mm=x_off + 500.0, y_mm=4000.0),
                width_mm=1000.0,
                angle_deg=0.0,
            )
        )

        # 6 south rooms (y=0..4000, width 3000 mm each = 12 m²)
        south_specs = [
            ("Office S1", RoomType.OPEN_OFFICE),
            ("Office S2", RoomType.OPEN_OFFICE),
            ("Office S3", RoomType.OPEN_OFFICE),
            ("Meeting S4", RoomType.MEETING_ROOM),
            ("Kitchenette S5", RoomType.KITCHEN),
            ("Utility S6", RoomType.UTILITY),
        ]
        for s_idx, (lbl, rtype) in enumerate(south_specs):
            x0 = x_off + s_idx * 3000.0
            x1 = x0 + 3000.0
            room_seq += 1
            rid = f"{flr}-R{room_seq}"
            rooms.append(
                Room(
                    id=rid,
                    raw_label=f"{lbl} ({flr})",
                    normalized_type=rtype,
                    floor=flr,
                    polygon_mm=[
                        Point2D(x_mm=x0, y_mm=0.0),
                        Point2D(x_mm=x1, y_mm=0.0),
                        Point2D(x_mm=x1, y_mm=4000.0),
                        Point2D(x_mm=x0, y_mm=4000.0),
                    ],
                    area_m2=12.0,
                    ceiling_height_mm=2900.0,
                    is_wet_room=rtype in (RoomType.BATHROOM, RoomType.WC),
                )
            )
            door_seq += 1
            doors.append(
                DoorOpening(
                    id=f"D{door_seq}",
                    room_id=rid,
                    hinge=Point2D(x_mm=x0 + 1050.0, y_mm=4000.0),
                    width_mm=900.0,
                    angle_deg=180.0,
                )
            )
            win_seq += 1
            windows.append(
                WindowOpening(
                    id=f"W{win_seq}",
                    room_id=rid,
                    start=Point2D(x_mm=x0 + 800.0, y_mm=0.0),
                    end=Point2D(x_mm=x0 + 2200.0, y_mm=0.0),
                )
            )

        # 5 north rooms (y=6000..10000, widths 3600 mm each = 14.4 m²)
        north_specs = [
            ("Office N1", RoomType.OPEN_OFFICE),
            ("Office N2", RoomType.OPEN_OFFICE),
            ("Office N3", RoomType.OPEN_OFFICE),
            (
                "Server Room N4" if f_idx == 0 else "Office N4",
                RoomType.SERVER_ROOM if f_idx == 0 else RoomType.OPEN_OFFICE,
            ),
            ("WC N5", RoomType.WC),
        ]
        for n_idx, (lbl, rtype) in enumerate(north_specs):
            x0 = x_off + n_idx * 3600.0
            x1 = x0 + 3600.0
            room_seq += 1
            rid = f"{flr}-R{room_seq}"
            rooms.append(
                Room(
                    id=rid,
                    raw_label=f"{lbl} ({flr})",
                    normalized_type=rtype,
                    floor=flr,
                    polygon_mm=[
                        Point2D(x_mm=x0, y_mm=6000.0),
                        Point2D(x_mm=x1, y_mm=6000.0),
                        Point2D(x_mm=x1, y_mm=10000.0),
                        Point2D(x_mm=x0, y_mm=10000.0),
                    ],
                    area_m2=14.4,
                    ceiling_height_mm=2900.0,
                    is_wet_room=(rtype == RoomType.WC),
                )
            )
            door_seq += 1
            doors.append(
                DoorOpening(
                    id=f"D{door_seq}",
                    room_id=rid,
                    hinge=Point2D(x_mm=x0 + 1350.0, y_mm=6000.0),
                    width_mm=900.0,
                    angle_deg=0.0,
                )
            )
            win_seq += 1
            windows.append(
                WindowOpening(
                    id=f"W{win_seq}",
                    room_id=rid,
                    start=Point2D(x_mm=x0 + 1050.0, y_mm=10000.0),
                    end=Point2D(x_mm=x0 + 2550.0, y_mm=10000.0),
                )
            )

    assert len(rooms) == 48
    return BuildingModel(
        project_name="Cyprus 4-Storey 48-Room Stress Building (720 m²)",
        building_type=BuildingType.OFFICE,
        total_floor_area_m2=sum(r.area_m2 for r in rooms),
        rooms=rooms,
        doors=doors,
        windows=windows,
        db_anchor=DistributionBoardAnchor(
            id="MDB",
            location_mm=Point2D(x_mm=400.0, y_mm=5000.0),
        ),
        sub_boards=sub_boards,
    )


def _record_perf(key: str, data: dict[str, Any]) -> None:
    _PERF_METRICS[key] = data
    report_path = os.environ.get("CYELEC_PERF_REPORT")
    if report_path:
        p = Path(report_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(_PERF_METRICS, indent=2), encoding="utf-8")


@pytest.mark.perf
def test_perf_apartment_3bed_latency_and_memory_budget(tmp_path: Path) -> None:
    """Tier-1: 3-Bed Apartment per-stage timing, total solve < 3.0 s & tracemalloc < 250 MiB."""
    from cyelec.render.floorplan_svg import render_floorplan_svg
    from cyelec.render.sld import render_sld_svg
    from cyelec.solver.grouper import generate_circuits
    from cyelec.spatial.placement import build_switch_legs, place_electrical_devices
    from cyelec.spatial.router import route_circuits
    from cyelec.spatial.wet_zones import compute_building_wet_zones
    from cyelec.standards_loader import load_standards

    dxf_path = tmp_path / "apt_3bed.dxf"
    generate_benchmark_dxf(BenchmarkTemplate.APARTMENT_3BED, out_dxf=dxf_path)

    # Warm standards cache first
    run_phase2_pipeline(dxf_path)

    # 1. Pure wall-clock per-stage & end-to-end timing (without tracemalloc overhead)
    t0 = time.perf_counter()
    bldg, flags = ingest_dxf(dxf_path)
    t_ing = time.perf_counter()

    std = load_standards()
    specs = generate_circuits(bldg, std)
    wz = compute_building_wet_zones(bldg, std)
    devs, _ = place_electrical_devices(bldg, wz, std)
    _ = build_switch_legs(devs)
    t_place = time.perf_counter()

    _ = route_circuits(bldg, specs, devs)
    t_route = time.perf_counter()

    t_s0 = time.perf_counter()
    res = run_phase2_pipeline(bldg, extra_flags=flags)
    t_solve = time.perf_counter()

    _ = render_floorplan_svg(res, discipline="combined")
    _ = render_sld_svg(res.design)
    t_render = time.perf_counter()

    # 2. Python heap peak memory pass
    tracemalloc.start()
    run_phase2_pipeline(dxf_path)
    _, peak_bytes = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    total_s = (t_ing - t0) + (t_solve - t_s0)
    peak_mib = peak_bytes / (1024 * 1024)
    _record_perf(
        "apartment_3bed",
        {
            "ingest_ms": round((t_ing - t0) * 1000, 1),
            "placement_ms": round((t_place - t_ing) * 1000, 1),
            "routing_ms": round((t_route - t_place) * 1000, 1),
            "solve_ms": round((t_solve - t_s0) * 1000, 1),
            "render_svg_ms": round((t_render - t_solve) * 1000, 1),
            "total_s": round(total_s, 3),
            "peak_tracemalloc_mib": round(peak_mib, 2),
            "circuits": len(res.design.circuits),
            "devices": len(res.spatial.devices),
        },
    )
    assert total_s < 3.0, f"apartment_3bed solve exceeded 3.0s safety ceiling: {total_s:.2f}s"
    assert peak_mib < 250.0, f"apartment_3bed tracemalloc peak exceeded 250 MiB: {peak_mib:.1f} MiB"


@pytest.mark.perf
def test_perf_villa_2storey_turnkey_and_phase3_pack_budget(tmp_path: Path) -> None:
    """Tier-1: 2-Storey Villa Turnkey Phase 2 < 5.0 s and Phase 3 DXF/SVG/XLSX pack < 10.0 s."""
    dxf_path = tmp_path / "villa_2storey.dxf"
    generate_benchmark_dxf(BenchmarkTemplate.VILLA_2STOREY, out_dxf=dxf_path)
    ov = DesignOverrides(
        pv_capacity_kva=6.0,
        residential_brief=ResidentialSystemsBrief(
            hvac_mode="heat_pump_ufh",
            heat_pump_kw=6.0,
            include_pool=True,
            include_knx=True,
            ev_charger_kw=11.0,
            building_height_m=12.0,
        ),
    )

    t0 = time.perf_counter()
    res = run_phase2_pipeline(dxf_path, overrides=ov)
    t_solve = time.perf_counter()
    pack_dir = tmp_path / "pack"
    export_phase3_deliverables(res, pack_dir, source_dxf_path=dxf_path)
    t_export = time.perf_counter()

    solve_s = t_solve - t0
    export_s = t_export - t_solve
    _record_perf(
        "villa_2storey_turnkey",
        {
            "solve_s": round(solve_s, 3),
            "phase3_export_s": round(export_s, 3),
            "circuits": len(res.design.circuits),
            "devices": len(res.spatial.devices),
        },
    )
    assert solve_s < 5.0, f"villa_2storey turnkey solve exceeded 5.0s ceiling: {solve_s:.2f}s"
    assert export_s < 10.0, f"villa_2storey Phase 3 export exceeded 10.0s ceiling: {export_s:.2f}s"


@pytest.mark.perf
def test_perf_large_48_room_4storey_stress_plan(tmp_path: Path) -> None:
    """Tier-1 Stress: 48-room, 4-storey (720 m²) commercial plan < 15.0 s, 0 stacked, 100% lux."""
    import resource

    bldg = _build_large_48_room_4storey_building()

    # Pure wall-clock solve & Phase 3 export timing plus OS peak RSS delta (Linux ru_maxrss KiB)
    rss_before_kib = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    t0 = time.perf_counter()
    res = run_phase2_pipeline(bldg)
    t_solve = time.perf_counter()
    pack_dir = tmp_path / "stress_pack"
    outputs = export_phase3_deliverables(res, pack_dir)
    t_export = time.perf_counter()
    rss_after_kib = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss

    solve_s = t_solve - t0
    export_s = t_export - t_solve
    peak_mib = max(0.0, (rss_after_kib - rss_before_kib) / 1024.0)

    # Verify zero stacked devices (<100mm) and 100% CIBSE lux compliance even at 48 rooms!
    room_floor = {r.id: r.floor for r in res.design.building.rooms}
    devs = res.spatial.devices
    stacked = 0
    for i in range(len(devs)):
        di = devs[i]
        fi = room_floor.get(di.room_id, "")
        for j in range(i + 1, len(devs)):
            dj = devs[j]
            if room_floor.get(dj.room_id, "") != fi:
                continue
            if math.hypot(
                di.location_mm.x_mm - dj.location_mm.x_mm,
                di.location_mm.y_mm - dj.location_mm.y_mm,
            ) < 100.0:
                stacked += 1

    non_compliant_lux = [lc.room_label for lc in res.design.lumen_checks if not lc.compliant]
    e201_doc = ezdxf.readfile(str(outputs["e201_sld_dxf"]))
    sld_errors = len(e201_doc.audit().errors)

    _record_perf(
        "stress_48_room_4storey",
        {
            "rooms": len(res.design.building.rooms),
            "circuits": len(res.design.circuits),
            "devices": len(devs),
            "solve_s": round(solve_s, 3),
            "export_s": round(export_s, 3),
            "rss_delta_mib": round(peak_mib, 2),
            "stacked_lt_100mm": stacked,
            "non_compliant_lux": len(non_compliant_lux),
            "sld_dxf_errors": sld_errors,
        },
    )

    assert solve_s < 15.0, f"48-room stress solve exceeded 15.0s ceiling: {solve_s:.2f}s"
    assert export_s < 25.0, f"48-room stress Phase 3 export exceeded 25.0s: {export_s:.2f}s"
    assert peak_mib < 250.0, f"48-room stress tracemalloc exceeded 250 MiB: {peak_mib:.1f} MiB"
    assert stacked == 0, f"48-room stress had {stacked} stacked devices (<100mm)"
    assert non_compliant_lux == [], f"48-room stress non-compliant lux: {non_compliant_lux[:5]}"
    assert sld_errors == 0
