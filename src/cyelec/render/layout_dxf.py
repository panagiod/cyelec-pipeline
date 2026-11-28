"""Stampable R2018 DXF electrical overlay renderer (IEC 60617 blocks, true-scale ISO sheets)."""

from __future__ import annotations

import itertools
import logging
import math
from collections import defaultdict
from collections.abc import Sequence
from contextlib import suppress
from pathlib import Path
from typing import Literal

from ezdxf import recover as ezdxf_recover
from ezdxf.addons.importer import Importer
from ezdxf.document import Drawing
from ezdxf.filemanagement import new as new_dxf
from ezdxf.filemanagement import readfile as read_dxf
from ezdxf.layouts.layout import Modelspace
from ezdxf.lldxf.const import DXFError

from cyelec.models.building import Room
from cyelec.models.electrical import CircuitCategory
from cyelec.models.spatial import DeviceType, Phase2DesignResult, PlacedDevice, SpatialLayoutResult
from cyelec.render.text_fit import dxf_char_budget, wrap_text_chars
from cyelec.synth.sample_plan import render_building_to_dxf

_logger = logging.getLogger(__name__)

LayoutDiscipline = Literal["combined", "lighting", "power", "elv"]


def _read_dxf_safe(path: str | Path) -> Drawing:
    """Read a DXF underlay, falling back to ezdxf.recover for quirky dwg2dxf exports."""
    try:
        return read_dxf(str(path))
    except DXFError:
        doc, _auditor = ezdxf_recover.readfile(str(path))
        return doc


def _clone_underlay_to_r2018(src_doc: Drawing) -> Drawing:
    """Clone architect modelspace into a pristine R2018 Drawing for reliable Paper Space export."""
    target = new_dxf("R2018", setup=True)
    try:
        target.header["$INSUNITS"] = int(src_doc.header.get("$INSUNITS", 4))
        target.header["$MEASUREMENT"] = int(src_doc.header.get("$MEASUREMENT", 1))
    except Exception:
        target.header["$INSUNITS"] = 4
        target.header["$MEASUREMENT"] = 1
    for lyr in src_doc.layers:
        lname = str(lyr.dxf.name)
        if lname and lname not in target.layers:
            try:
                target.layers.add(lname, color=int(getattr(lyr.dxf, "color", 7)))
            except Exception:
                _logger.debug("Skipping underlay layer '%s' during R2018 clone.", lname)
                continue
    t_msp = target.modelspace()
    importer = Importer(src_doc, target)
    for ent in list(src_doc.modelspace()):
        try:
            importer.import_entity(ent, t_msp)
        except Exception:
            _logger.debug("Skipping non-importable underlay entity '%s'.", ent.dxftype())
            continue
    with suppress(Exception):
        importer.finalize()
    return target


ELECTRICAL_LAYERS: dict[str, int] = {
    "E-WETZONE-Z0": 1,  # Red: Section 701 Zone 0/1
    "E-WETZONE-Z2": 30,  # Orange: Section 701 Zone 2 (600 mm)
    "E-WETZONE-EXCL": 6,  # Magenta: EAC socket exclusion (2.5 m / 3.0 m)
    "E-LITE": 2,  # Yellow: Ceiling luminaires, pendants, mirror lights & fans
    "E-SWIT": 3,  # Green: Light switches, dimmers & 3P fan isolators
    "E-SWIT-LEG": 94,  # Light green: Dashed switch-to-luminaire control arcs
    "E-SOCK": 4,  # Cyan: Twin 13A sockets & kitchen FCUs
    "E-AC": 5,  # Blue: Split A/C, VRV, Heat Pump & thermostats
    "E-APPL": 210,  # Purple: Dedicated appliances, shutters, CCU, EV, Pool & pumps
    "E-EARTHING": 92,  # Green-Yellow: EAC Meter Cabinet & TT Earth Pit
    "E-COND-LITE": 50,  # Warm yellow-amber: Lighting home-run arrows & loop arcs
    "E-COND-PWR": 140,  # Cool cyan-blue: Socket/ring power home-run arrows & arcs
    "E-COND-DED": 200,  # Violet: Dedicated A/C, cooker, EV, PV, 3P home-runs
    "E-TITLE": 7,  # White/Black: EAC / ETEK CAD title block & legend
}

STAGE5_EXTRA_LAYERS: dict[str, int] = {
    "E-ROUTE-CONDUIT": 8,  # Grey (OFF by default): Full NetworkX wall/ceiling routed paths
    "E-ELV-DATA": 150,  # Deep blue: Cat6 Data, TV, Wi-Fi AP & 9U Telecom Rack
    "E-ELV-SEC": 220,  # Violet-red: Video Intercom, Intruder Alarm & CCTV
    "E-ELV-SMOKE": 30,  # Orange: BS 5839-6 Interlinked Smoke & Heat Detectors
    "E-KNX": 82,  # Emerald: Optional KNX Bus & Scene Keypads
}

_LIGHTING_DEVICE_TYPES: frozenset[DeviceType] = frozenset(
    {
        DeviceType.LUMINAIRE,
        DeviceType.PENDANT_LIGHT,
        DeviceType.WALL_SCONCE,
        DeviceType.LINEAR_COVE,
        DeviceType.OUTDOOR_PIR_LIGHT,
        DeviceType.GARDEN_BOLLARD,
        DeviceType.MIRROR_LIGHT,
        DeviceType.EXHAUST_FAN,
        DeviceType.SWITCH,
        DeviceType.DIMMER_SWITCH,
        DeviceType.FAN_ISOLATOR_3P,
    }
)

_POWER_DEVICE_TYPES: frozenset[DeviceType] = frozenset(
    {
        DeviceType.SOCKET_TWIN,
        DeviceType.KITCHEN_FCU,
        DeviceType.SPLIT_AC,
        DeviceType.SHUTTER_SWITCH,
        DeviceType.HEAT_PUMP_OUTDOOR,
        DeviceType.HEAT_PUMP_HYDROBOX,
        DeviceType.UFH_MANIFOLD,
        DeviceType.ROOM_THERMOSTAT,
        DeviceType.VRV_OUTDOOR,
        DeviceType.VRV_INDOOR_ISOLATOR,
        DeviceType.WATER_BOOSTER_PUMP,
        DeviceType.GATE_MOTOR,
        DeviceType.EV_WALLBOX,
        DeviceType.POOL_SUB_DB,
        DeviceType.COOKER_CONTROL_UNIT,
        DeviceType.COOKER_OUTLET,
        DeviceType.OVEN_OUTLET,
        DeviceType.APPLIANCE_OUTLET,
        DeviceType.WATER_HEATER_DP_SWITCH,
        DeviceType.WATER_HEATER_ISOLATOR,
        DeviceType.EAC_METER_CABINET,
        DeviceType.EARTH_PIT,
    }
)

_ELV_DEVICE_TYPES: frozenset[DeviceType] = frozenset(
    {
        DeviceType.TELECOM_CABINET,
        DeviceType.DATA_RJ45,
        DeviceType.TV_OUTLET,
        DeviceType.WIFI_AP,
        DeviceType.INTERCOM_OUTDOOR,
        DeviceType.INTERCOM_INDOOR,
        DeviceType.ALARM_PANEL,
        DeviceType.ALARM_PIR,
        DeviceType.ALARM_KEYPAD,
        DeviceType.ALARM_SIREN,
        DeviceType.CCTV_CAMERA,
        DeviceType.SMOKE_DETECTOR,
        DeviceType.HEAT_DETECTOR,
        DeviceType.KNX_KEYPAD,
    }
)

_BLOCK_NAMES: dict[DeviceType, str] = {
    DeviceType.LUMINAIRE: "CYELEC_LUMINAIRE",
    DeviceType.PENDANT_LIGHT: "CYELEC_PENDANT",
    DeviceType.WALL_SCONCE: "CYELEC_MIRROR_LIGHT",
    DeviceType.LINEAR_COVE: "CYELEC_LUMINAIRE",
    DeviceType.OUTDOOR_PIR_LIGHT: "CYELEC_OUTDOOR_PIR",
    DeviceType.GARDEN_BOLLARD: "CYELEC_LUMINAIRE",
    DeviceType.MIRROR_LIGHT: "CYELEC_MIRROR_LIGHT",
    DeviceType.EXHAUST_FAN: "CYELEC_EXHAUST_FAN",
    DeviceType.SWITCH: "CYELEC_SWITCH",
    DeviceType.DIMMER_SWITCH: "CYELEC_DIMMER",
    DeviceType.FAN_ISOLATOR_3P: "CYELEC_FAN_ISOL",
    DeviceType.SOCKET_TWIN: "CYELEC_SOCKET_13A",
    DeviceType.KITCHEN_FCU: "CYELEC_DP_20A",
    DeviceType.SPLIT_AC: "CYELEC_AC_ISOL",
    DeviceType.SHUTTER_SWITCH: "CYELEC_SHUTTER",
    DeviceType.HEAT_PUMP_OUTDOOR: "CYELEC_PLANT_BOX",
    DeviceType.HEAT_PUMP_HYDROBOX: "CYELEC_PLANT_BOX",
    DeviceType.UFH_MANIFOLD: "CYELEC_PLANT_BOX",
    DeviceType.ROOM_THERMOSTAT: "CYELEC_THERMOSTAT",
    DeviceType.VRV_OUTDOOR: "CYELEC_PLANT_BOX",
    DeviceType.VRV_INDOOR_ISOLATOR: "CYELEC_AC_ISOL",
    DeviceType.WATER_BOOSTER_PUMP: "CYELEC_PLANT_BOX",
    DeviceType.GATE_MOTOR: "CYELEC_PLANT_BOX",
    DeviceType.EV_WALLBOX: "CYELEC_EV_BOX",
    DeviceType.POOL_SUB_DB: "CYELEC_DB",
    DeviceType.COOKER_CONTROL_UNIT: "CYELEC_CCU_45A",
    DeviceType.COOKER_OUTLET: "CYELEC_APPL",
    DeviceType.OVEN_OUTLET: "CYELEC_APPL",
    DeviceType.APPLIANCE_OUTLET: "CYELEC_APPL",
    DeviceType.WATER_HEATER_DP_SWITCH: "CYELEC_DP_20A",
    DeviceType.WATER_HEATER_ISOLATOR: "CYELEC_APPL",
    DeviceType.EAC_METER_CABINET: "CYELEC_METER_BOX",
    DeviceType.EARTH_PIT: "CYELEC_EARTH_PIT",
    DeviceType.TELECOM_CABINET: "CYELEC_RACK",
    DeviceType.DATA_RJ45: "CYELEC_DATA",
    DeviceType.TV_OUTLET: "CYELEC_TV",
    DeviceType.WIFI_AP: "CYELEC_WIFI",
    DeviceType.INTERCOM_OUTDOOR: "CYELEC_INTERCOM",
    DeviceType.INTERCOM_INDOOR: "CYELEC_INTERCOM",
    DeviceType.ALARM_PANEL: "CYELEC_RACK",
    DeviceType.ALARM_PIR: "CYELEC_PIR",
    DeviceType.ALARM_KEYPAD: "CYELEC_INTERCOM",
    DeviceType.ALARM_SIREN: "CYELEC_SIREN",
    DeviceType.CCTV_CAMERA: "CYELEC_CCTV",
    DeviceType.SMOKE_DETECTOR: "CYELEC_SMOKE",
    DeviceType.HEAT_DETECTOR: "CYELEC_SMOKE",
    DeviceType.KNX_KEYPAD: "CYELEC_KNX",
}


def _resolve_original_dxf_path(
    result: Phase2DesignResult,
    source_dxf_path: Path | None = None,
) -> Path | None:
    if source_dxf_path is not None and source_dxf_path.is_file():
        return source_dxf_path if source_dxf_path.suffix.lower() == ".dxf" else None
    src = result.design.building.source_file
    if not src or "," in src:
        return None
    candidate = Path(src)
    if candidate.is_file() and candidate.suffix.lower() == ".dxf":
        return candidate
    return None


def _build_multifloor_underlay_doc(result: Phase2DesignResult) -> Drawing | None:
    """Merge each floor's original architect DXF into mm modelspace at exact dx_mm."""
    refs = [
        ref
        for ref in result.design.building.floor_underlays
        if Path(ref.source_file).is_file() and Path(ref.source_file).suffix.lower() == ".dxf"
    ]
    if len(refs) < 2:
        return None

    target = new_dxf("R2018", setup=True)
    target.header["$INSUNITS"] = 4
    target.header["$MEASUREMENT"] = 1
    t_msp = target.modelspace()
    merged_any = False

    for idx, ref in enumerate(refs):
        try:
            src_doc = _read_dxf_safe(ref.source_file)
            for lyr in src_doc.layers:
                lname = str(lyr.dxf.name)
                if lname and lname not in target.layers:
                    target.layers.add(lname, color=int(getattr(lyr.dxf, "color", 7)))
            blk_name = f"ARCH_FLOOR_{idx}_{ref.floor[:8]}"
            if blk_name in target.blocks:
                target.blocks.delete_block(blk_name, safe=False)
            importer = Importer(src_doc, target)
            # Copy source modelspace into a dedicated floor block, then insert scaled+shifted
            tmp_blk = src_doc.blocks.new(name=blk_name)
            for ent in list(src_doc.modelspace()):
                try:
                    tmp_blk.add_entity(ent.copy())
                except Exception:
                    _logger.debug("Skipping non-copyable underlay entity '%s'.", ent.dxftype())
                    continue
            importer.import_block(blk_name)
            importer.finalize()
            s = ref.unit_scale_to_mm
            t_msp.add_blockref(
                blk_name,
                (ref.dx_mm, 0.0),
                dxfattribs={"xscale": s, "yscale": s, "zscale": s},
            )
            merged_any = True
        except (OSError, DXFError):
            continue

    return target if merged_any else None


def _ensure_iec_blocks(doc: Drawing, inv_scale: float) -> None:
    """Define reusable named IEC 60617 AutoCAD BLOCK symbols scaled to modelspace units."""

    def _s(v_mm: float) -> float:
        return v_mm * inv_scale

    # 1. Ceiling Luminaire (circle + 45-deg cross)
    if "CYELEC_LUMINAIRE" not in doc.blocks:
        blk = doc.blocks.new(name="CYELEC_LUMINAIRE")
        r, d = _s(120.0), _s(85.0)
        blk.add_circle((0.0, 0.0), radius=r, dxfattribs={"layer": "0"})
        blk.add_line((-d, -d), (d, d), dxfattribs={"layer": "0"})
        blk.add_line((-d, d), (d, -d), dxfattribs={"layer": "0"})

    # 2. Light Switch (IEC 60617 circle + angled lever + terminal tick)
    if "CYELEC_SWITCH" not in doc.blocks:
        blk = doc.blocks.new(name="CYELEC_SWITCH")
        blk.add_circle((0.0, 0.0), radius=_s(65.0), dxfattribs={"layer": "0"})
        blk.add_line((0.0, 0.0), (_s(120.0), _s(120.0)), dxfattribs={"layer": "0"})
        blk.add_line((_s(120.0), _s(120.0)), (_s(155.0), _s(85.0)), dxfattribs={"layer": "0"})

    # 3. Twin 13A BS 1363 Socket (wall-aligned semicircle + base bar + twin ticks)
    if "CYELEC_SOCKET_13A" not in doc.blocks:
        blk = doc.blocks.new(name="CYELEC_SOCKET_13A")
        sr = _s(90.0)
        blk.add_arc(
            (0.0, 0.0), radius=sr, start_angle=0.0, end_angle=180.0, dxfattribs={"layer": "0"}
        )
        blk.add_line((-sr, 0.0), (sr, 0.0), dxfattribs={"layer": "0"})
        blk.add_line((0.0, sr), (0.0, sr + _s(60.0)), dxfattribs={"layer": "0"})
        blk.add_line(
            (-_s(35.0), sr + _s(35.0)), (_s(35.0), sr + _s(35.0)), dxfattribs={"layer": "0"}
        )

    # 4. Split A/C Isolator (wall-aligned box + diagonal + AC mark)
    if "CYELEC_AC_ISOL" not in doc.blocks:
        blk = doc.blocks.new(name="CYELEC_AC_ISOL")
        rx, ry = _s(150.0), _s(75.0)
        blk.add_lwpolyline(
            [(-rx, -ry), (rx, -ry), (rx, ry), (-rx, ry)],
            close=True,
            dxfattribs={"layer": "0"},
        )
        blk.add_line((-rx, -ry), (rx, ry), dxfattribs={"layer": "0"})

    # 5. Dedicated Appliance Isolator / FCU (square + inner diamond)
    if "CYELEC_APPL" not in doc.blocks:
        blk = doc.blocks.new(name="CYELEC_APPL")
        rr = _s(95.0)
        blk.add_lwpolyline(
            [(-rr, -rr), (rr, -rr), (rr, rr), (-rr, rr)],
            close=True,
            dxfattribs={"layer": "0"},
        )
        blk.add_lwpolyline(
            [(0.0, -rr), (rr, 0.0), (0.0, rr), (-rr, 0.0)],
            close=True,
            dxfattribs={"layer": "0"},
        )

    # 6. Distribution Board IEC 60617 half-filled rectangle
    if "CYELEC_DB" not in doc.blocks:
        blk = doc.blocks.new(name="CYELEC_DB")
        w, h = _s(220.0), _s(110.0)
        blk.add_lwpolyline(
            [(-w, -h), (w, -h), (w, h), (-w, h)],
            close=True,
            dxfattribs={"layer": "0"},
        )
        blk.add_line((-w, -h), (w, h), dxfattribs={"layer": "0"})
        blk.add_line((-w, 0.0), (0.0, h), dxfattribs={"layer": "0"})

    # 7. 2-Gang Switch (IEC circle + dual-prong lever)
    if "CYELEC_SWITCH_2G" not in doc.blocks:
        blk = doc.blocks.new(name="CYELEC_SWITCH_2G")
        blk.add_circle((0.0, 0.0), radius=_s(65.0), dxfattribs={"layer": "0"})
        blk.add_line((0.0, 0.0), (_s(135.0), _s(135.0)), dxfattribs={"layer": "0"})
        blk.add_line((_s(100.0), _s(100.0)), (_s(135.0), _s(65.0)), dxfattribs={"layer": "0"})
        blk.add_line((_s(135.0), _s(135.0)), (_s(170.0), _s(100.0)), dxfattribs={"layer": "0"})

    # 8. 2-Way Switch (IEC circle + opposing levers)
    if "CYELEC_SWITCH_2W" not in doc.blocks:
        blk = doc.blocks.new(name="CYELEC_SWITCH_2W")
        blk.add_circle((0.0, 0.0), radius=_s(65.0), dxfattribs={"layer": "0"})
        blk.add_line((-_s(110.0), -_s(110.0)), (_s(110.0), _s(110.0)), dxfattribs={"layer": "0"})
        blk.add_line((_s(110.0), _s(110.0)), (_s(145.0), _s(75.0)), dxfattribs={"layer": "0"})
        blk.add_line((-_s(110.0), -_s(110.0)), (-_s(145.0), -_s(75.0)), dxfattribs={"layer": "0"})

    # 9. IP44 Vanity Mirror Light (wall bar + centered luminaire)
    if "CYELEC_MIRROR_LIGHT" not in doc.blocks:
        blk = doc.blocks.new(name="CYELEC_MIRROR_LIGHT")
        blk.add_line((-_s(130.0), 0.0), (_s(130.0), 0.0), dxfattribs={"layer": "0"})
        blk.add_circle((0.0, _s(65.0)), radius=_s(55.0), dxfattribs={"layer": "0"})
        blk.add_line((-_s(40.0), _s(25.0)), (_s(40.0), _s(105.0)), dxfattribs={"layer": "0"})

    # 10. Bathroom Exhaust Fan (circle + 3 rotor blades)
    if "CYELEC_EXHAUST_FAN" not in doc.blocks:
        blk = doc.blocks.new(name="CYELEC_EXHAUST_FAN")
        r = _s(110.0)
        blk.add_circle((0.0, 0.0), radius=r, dxfattribs={"layer": "0"})
        for deg in (0.0, 120.0, 240.0):
            rad = math.radians(deg)
            blk.add_line(
                (0.0, 0.0),
                (r * math.cos(rad), r * math.sin(rad)),
                dxfattribs={"layer": "0"},
            )

    # 11. 3-Pole Fan Isolator (square + triple slash)
    if "CYELEC_FAN_ISOL" not in doc.blocks:
        blk = doc.blocks.new(name="CYELEC_FAN_ISOL")
        rr = _s(85.0)
        blk.add_lwpolyline(
            [(-rr, -rr), (rr, -rr), (rr, rr), (-rr, rr)],
            close=True,
            dxfattribs={"layer": "0"},
        )
        blk.add_line((-rr, -rr), (rr, rr), dxfattribs={"layer": "0"})
        blk.add_line((-rr, 0.0), (0.0, rr), dxfattribs={"layer": "0"})
        blk.add_line((0.0, -rr), (rr, 0.0), dxfattribs={"layer": "0"})

    # 12. 45A DP Cooker Control Unit (double-frame box + neon indicator circle)
    if "CYELEC_CCU_45A" not in doc.blocks:
        blk = doc.blocks.new(name="CYELEC_CCU_45A")
        rx, ry = _s(120.0), _s(85.0)
        blk.add_lwpolyline(
            [(-rx, -ry), (rx, -ry), (rx, ry), (-rx, ry)],
            close=True,
            dxfattribs={"layer": "0"},
        )
        blk.add_circle((-_s(45.0), 0.0), radius=_s(30.0), dxfattribs={"layer": "0"})
        blk.add_line((_s(15.0), -ry), (_s(15.0), ry), dxfattribs={"layer": "0"})

    # 13. 20A DP Water Heater Switch + Neon (square + inner neon circle + slash)
    if "CYELEC_DP_20A" not in doc.blocks:
        blk = doc.blocks.new(name="CYELEC_DP_20A")
        rr = _s(90.0)
        blk.add_lwpolyline(
            [(-rr, -rr), (rr, -rr), (rr, rr), (-rr, rr)],
            close=True,
            dxfattribs={"layer": "0"},
        )
        blk.add_circle((0.0, 0.0), radius=_s(42.0), dxfattribs={"layer": "0"})
        blk.add_line((-rr, -rr), (rr, rr), dxfattribs={"layer": "0"})

    # 14. EAC Meter Cabinet (Kibwtio Metrhth AHK)
    if "CYELEC_METER_BOX" not in doc.blocks:
        blk = doc.blocks.new(name="CYELEC_METER_BOX")
        rx, ry = _s(210.0), _s(130.0)
        blk.add_lwpolyline(
            [(-rx, -ry), (rx, -ry), (rx, ry), (-rx, ry)],
            close=True,
            dxfattribs={"layer": "0"},
        )
        blk.add_lwpolyline(
            [
                (-_s(90.0), -_s(60.0)),
                (-_s(90.0), _s(60.0)),
                (0.0, 0.0),
                (_s(90.0), _s(60.0)),
                (_s(90.0), -_s(60.0)),
            ],
            dxfattribs={"layer": "0"},
        )

    # 15. TT Earth Inspection Pit (circle + IEC 60617 earth symbol)
    if "CYELEC_EARTH_PIT" not in doc.blocks:
        blk = doc.blocks.new(name="CYELEC_EARTH_PIT")
        r = _s(145.0)
        blk.add_circle((0.0, 0.0), radius=r, dxfattribs={"layer": "0"})
        blk.add_line((0.0, _s(85.0)), (0.0, -_s(15.0)), dxfattribs={"layer": "0"})
        blk.add_line((-_s(75.0), -_s(15.0)), (_s(75.0), -_s(15.0)), dxfattribs={"layer": "0"})
        blk.add_line((-_s(50.0), -_s(48.0)), (_s(50.0), -_s(48.0)), dxfattribs={"layer": "0"})
        blk.add_line((-_s(25.0), -_s(80.0)), (_s(25.0), -_s(80.0)), dxfattribs={"layer": "0"})

    # 16. Stage 5 Blocks (Pendant, Dimmer, Outdoor PIR, Shutter, Plant, EV, ELV, Smoke & KNX)
    def _box_block(name: str, w: float, h: float, diag: bool = False) -> None:
        if name in doc.blocks:
            return
        b = doc.blocks.new(name=name)
        hw, hh = _s(w), _s(h)
        b.add_lwpolyline(
            [(-hw, -hh), (hw, -hh), (hw, hh), (-hw, hh)], close=True, dxfattribs={"layer": "0"}
        )
        if diag:
            b.add_line((-hw, -hh), (hw, hh), dxfattribs={"layer": "0"})

    if "CYELEC_PENDANT" not in doc.blocks:
        b = doc.blocks.new(name="CYELEC_PENDANT")
        b.add_circle((0.0, 0.0), radius=_s(130.0), dxfattribs={"layer": "0"})
        b.add_circle((0.0, 0.0), radius=_s(65.0), dxfattribs={"layer": "0"})
    if "CYELEC_OUTDOOR_PIR" not in doc.blocks:
        b = doc.blocks.new(name="CYELEC_OUTDOOR_PIR")
        b.add_circle((0.0, 0.0), radius=_s(110.0), dxfattribs={"layer": "0"})
        b.add_arc(
            (0.0, 0.0),
            radius=_s(165.0),
            start_angle=210.0,
            end_angle=330.0,
            dxfattribs={"layer": "0"},
        )
    if "CYELEC_DIMMER" not in doc.blocks:
        b = doc.blocks.new(name="CYELEC_DIMMER")
        b.add_circle((0.0, 0.0), radius=_s(65.0), dxfattribs={"layer": "0"})
        b.add_line((0.0, 0.0), (_s(130.0), _s(130.0)), dxfattribs={"layer": "0"})
        b.add_line((-_s(90.0), -_s(90.0)), (_s(90.0), _s(90.0)), dxfattribs={"layer": "0"})
    if "CYELEC_SHUTTER" not in doc.blocks:
        b = doc.blocks.new(name="CYELEC_SHUTTER")
        r = _s(90.0)
        b.add_lwpolyline(
            [(-r, -r), (r, -r), (r, r), (-r, r)], close=True, dxfattribs={"layer": "0"}
        )
        b.add_line((0.0, -_s(55.0)), (0.0, _s(55.0)), dxfattribs={"layer": "0"})
        b.add_line((-_s(35.0), _s(25.0)), (0.0, _s(55.0)), dxfattribs={"layer": "0"})
        b.add_line((_s(35.0), _s(25.0)), (0.0, _s(55.0)), dxfattribs={"layer": "0"})
    if "CYELEC_THERMOSTAT" not in doc.blocks:
        b = doc.blocks.new(name="CYELEC_THERMOSTAT")
        b.add_circle((0.0, 0.0), radius=_s(95.0), dxfattribs={"layer": "0"})
        b.add_line((-_s(45.0), _s(40.0)), (_s(45.0), _s(40.0)), dxfattribs={"layer": "0"})
        b.add_line((0.0, _s(40.0)), (0.0, -_s(50.0)), dxfattribs={"layer": "0"})
    _box_block("CYELEC_PLANT_BOX", 180.0, 120.0, diag=True)
    _box_block("CYELEC_EV_BOX", 150.0, 110.0, diag=False)
    _box_block("CYELEC_RACK", 190.0, 115.0, diag=True)
    if "CYELEC_DATA" not in doc.blocks:
        b = doc.blocks.new(name="CYELEC_DATA")
        r = _s(100.0)
        b.add_lwpolyline([(0.0, r), (-r, -r), (r, -r)], close=True, dxfattribs={"layer": "0"})
    if "CYELEC_TV" not in doc.blocks:
        b = doc.blocks.new(name="CYELEC_TV")
        r = _s(95.0)
        b.add_lwpolyline(
            [(-r, -r), (r, -r), (r, r), (-r, r)], close=True, dxfattribs={"layer": "0"}
        )
        b.add_line((-_s(50.0), _s(40.0)), (_s(50.0), _s(40.0)), dxfattribs={"layer": "0"})
    if "CYELEC_WIFI" not in doc.blocks:
        b = doc.blocks.new(name="CYELEC_WIFI")
        b.add_circle((0.0, 0.0), radius=_s(45.0), dxfattribs={"layer": "0"})
        b.add_circle((0.0, 0.0), radius=_s(105.0), dxfattribs={"layer": "0"})
        b.add_circle((0.0, 0.0), radius=_s(160.0), dxfattribs={"layer": "0"})
    _box_block("CYELEC_INTERCOM", 105.0, 85.0, diag=False)
    if "CYELEC_PIR" not in doc.blocks:
        b = doc.blocks.new(name="CYELEC_PIR")
        b.add_arc(
            (0.0, 0.0), radius=_s(120.0), start_angle=0.0, end_angle=90.0, dxfattribs={"layer": "0"}
        )
        b.add_line((0.0, 0.0), (_s(120.0), 0.0), dxfattribs={"layer": "0"})
        b.add_line((0.0, 0.0), (0.0, _s(120.0)), dxfattribs={"layer": "0"})
    if "CYELEC_SIREN" not in doc.blocks:
        b = doc.blocks.new(name="CYELEC_SIREN")
        r = _s(110.0)
        b.add_lwpolyline([(-r, -r), (r, -r), (0.0, r)], close=True, dxfattribs={"layer": "0"})
    if "CYELEC_CCTV" not in doc.blocks:
        b = doc.blocks.new(name="CYELEC_CCTV")
        b.add_lwpolyline(
            [
                (-_s(120.0), -_s(60.0)),
                (_s(70.0), -_s(60.0)),
                (_s(70.0), _s(60.0)),
                (-_s(120.0), _s(60.0)),
            ],
            close=True,
            dxfattribs={"layer": "0"},
        )
        b.add_lwpolyline(
            [
                (_s(70.0), -_s(30.0)),
                (_s(140.0), -_s(65.0)),
                (_s(140.0), _s(65.0)),
                (_s(70.0), _s(30.0)),
            ],
            close=True,
            dxfattribs={"layer": "0"},
        )
    if "CYELEC_SMOKE" not in doc.blocks:
        b = doc.blocks.new(name="CYELEC_SMOKE")
        b.add_circle((0.0, 0.0), radius=_s(100.0), dxfattribs={"layer": "0"})
        b.add_circle((0.0, 0.0), radius=_s(50.0), dxfattribs={"layer": "0"})
    _box_block("CYELEC_KNX", 95.0, 95.0, diag=True)


def _conduit_layer_for_category(cat: CircuitCategory | None) -> str:
    if cat in (CircuitCategory.LIGHTING, CircuitCategory.EMERGENCY_LIGHTING):
        return "E-COND-LITE"
    if cat in (
        CircuitCategory.SOCKET_RING,
        CircuitCategory.SOCKET_RADIAL,
        CircuitCategory.UPS_SERVER,
    ):
        return "E-COND-PWR"
    return "E-COND-DED"


def _boxes_overlap(
    a: tuple[float, float, float, float],
    b: tuple[float, float, float, float],
    pad: float = 0.0,
) -> bool:
    return not (
        a[2] + pad <= b[0] or b[2] + pad <= a[0] or a[3] + pad <= b[1] or b[3] + pad <= a[1]
    )


def _place_decollided_label(
    x: float,
    y: float,
    text: str,
    text_h: float,
    occupied_boxes: list[tuple[float, float, float, float]],
    inv_scale: float,
) -> tuple[float, float]:
    """Select the first collision-free label insertion point around (x, y) deterministically."""
    w = max(text_h * 0.65 * len(text), text_h * 2.5)
    h = text_h * 1.25
    r1 = 155.0 * inv_scale
    r2 = 310.0 * inv_scale
    r3 = 470.0 * inv_scale
    pad = 25.0 * inv_scale

    candidates: list[tuple[float, float]] = []
    for rad in (r1, r2, r3):
        candidates.extend(
            [
                (x + rad, y + rad * 0.35),  # E-NE
                (x + rad, y - rad * 0.35 - h),  # E-SE
                (x - rad - w, y + rad * 0.35),  # W-NW
                (x - rad - w, y - rad * 0.35 - h),  # W-SW
                (x - w * 0.5, y + rad),  # N
                (x - w * 0.5, y - rad - h),  # S
                (x + rad * 0.7, y + rad * 0.85),  # NE
                (x - rad * 0.7 - w, y - rad * 0.85 - h),  # SW
            ]
        )

    for cx, cy in candidates:
        cand_box = (cx, cy, cx + w, cy + h)
        if not any(_boxes_overlap(cand_box, ob, pad=pad) for ob in occupied_boxes):
            occupied_boxes.append(cand_box)
            return cx, cy

    # Deterministic non-overlapping vertical stack fallback in extreme density corners
    fx, fy = candidates[0]
    while any(_boxes_overlap((fx, fy, fx + w, fy + h), ob, pad=pad) for ob in occupied_boxes):
        fy += h + pad * 2.0
    occupied_boxes.append((fx, fy, fx + w, fy + h))
    return fx, fy


def _add_dashed_curve(
    msp: Modelspace,
    p0: tuple[float, float],
    p1: tuple[float, float],
    *,
    layer: str,
    bulge_frac: float = 0.16,
    dash_count: int = 9,
) -> None:
    """Draw a clean quadratic Bezier dashed switch-leg control arc in Model Space."""
    x0, y0 = p0
    x1, y1 = p1
    dx, dy = x1 - x0, y1 - y0
    dist = math.hypot(dx, dy)
    if dist < 1e-6:
        return
    # Control point offset perpendicular to chord
    nx, ny = -dy / dist, dx / dist
    cx = (x0 + x1) * 0.5 + nx * (dist * bulge_frac)
    cy = (y0 + y1) * 0.5 + ny * (dist * bulge_frac)
    steps = max(6, dash_count * 2)
    pts: list[tuple[float, float]] = []
    for k in range(steps + 1):
        t = k / float(steps)
        mt = 1.0 - t
        px = mt * mt * x0 + 2.0 * mt * t * cx + t * t * x1
        py = mt * mt * y0 + 2.0 * mt * t * cy + t * t * y1
        pts.append((px, py))
    add_line = msp.add_line
    for k in range(0, len(pts) - 1, 2):
        add_line(pts[k], pts[k + 1], dxfattribs={"layer": layer})


def _add_dxf_wet_zones(
    msp: Modelspace,
    spatial: SpatialLayoutResult,
    inv_scale: float,
    discipline: LayoutDiscipline,
) -> None:
    """Add BS 7671 Section 701 wet-zone boundary polylines to modelspace."""
    def _s(v_mm: float) -> float:
        return round(v_mm * inv_scale, 4)

    # 1. Section 701 wet-zone polygons (Power & Combined sheets)
    if discipline in ("combined", "power"):
        for wz in spatial.wet_zones:
            for ring in wz.zone_0_polygons_mm:
                msp.add_lwpolyline(
                    [(_s(p.x_mm), _s(p.y_mm)) for p in ring],
                    close=True,
                    dxfattribs={"layer": "E-WETZONE-Z0"},
                )
            for ring in wz.zone_2_polygons_mm:
                msp.add_lwpolyline(
                    [(_s(p.x_mm), _s(p.y_mm)) for p in ring],
                    close=True,
                    dxfattribs={"layer": "E-WETZONE-Z2"},
                )
            for ring in wz.socket_exclusion_polygons_mm:
                msp.add_lwpolyline(
                    [(_s(p.x_mm), _s(p.y_mm)) for p in ring],
                    close=True,
                    dxfattribs={"layer": "E-WETZONE-EXCL"},
                )



def _add_dxf_conduit_routes(
    doc: Drawing,
    msp: Modelspace,
    result: Phase2DesignResult,
    inv_scale: float,
    discipline: LayoutDiscipline,
) -> tuple[dict[str, str], list[tuple[float, float, str, str]]]:
    """Add home-run arrows, loop arcs & NetworkX conduit routes; return floor map & anchors."""
    spatial = result.spatial
    db_anchor = result.design.building.db_anchor

    def _s(v_mm: float) -> float:
        return round(v_mm * inv_scale, 4)

    # 2. Cyprus Home-Run Arrows + Loop Arcs (full NetworkX routes kept on E-ROUTE-CONDUIT)
    circuit_map = {c.spec.circuit_id: c for c in result.design.circuits}
    circuit_cat_map = {c.spec.circuit_id: c.spec.category for c in result.design.circuits}
    room_floor: dict[str, str] = {r.id: r.floor for r in result.design.building.rooms}
    drafting_style = result.design.residential_brief.conduit_drafting_style
    use_homerun_style = drafting_style == "homerun_arcs"

    if use_homerun_style and "E-ROUTE-CONDUIT" in doc.layers:
        with suppress(Exception):
            doc.layers.get("E-ROUTE-CONDUIT").off()

    def _route_included(cid: str) -> bool:
        if discipline == "elv":
            return False
        cat = circuit_cat_map.get(cid)
        is_lite = cat in (CircuitCategory.LIGHTING, CircuitCategory.EMERGENCY_LIGHTING)
        if discipline == "lighting":
            return is_lite
        if discipline == "power":
            return not is_lite
        return True

    active_routes = [rc for rc in spatial.routed_circuits if _route_included(rc.circuit_id)]
    devs_by_id_all = {d.device_id: d for d in spatial.devices}
    board_locs: dict[str, tuple[float, float]] = {
        db_anchor.id: (db_anchor.location_mm.x_mm, db_anchor.location_mm.y_mm)
    }
    board_id_by_floor: dict[str, str] = {}
    for sb in result.design.building.sub_boards:
        board_locs[sb.board_id] = (sb.location_mm.x_mm, sb.location_mm.y_mm)
        board_id_by_floor[sb.floor] = sb.board_id

    # Index coincident corridor segments so parallel runs fan out perpendicularly (35 mm pitch)
    seg_circuits: dict[tuple[tuple[int, int], tuple[int, int]], list[str]] = defaultdict(list)
    for rc in active_routes:
        for seg in rc.segments:
            p1 = (round(seg.start_mm.x_mm / 50.0), round(seg.start_mm.y_mm / 50.0))
            p2 = (round(seg.end_mm.x_mm / 50.0), round(seg.end_mm.y_mm / 50.0))
            key = (p1, p2) if p1 <= p2 else (p2, p1)
            if rc.circuit_id not in seg_circuits[key]:
                seg_circuits[key].append(rc.circuit_id)

    pitch_mm = 35.0
    max_fan_mm = 210.0
    trunk_callout_anchors: list[tuple[float, float, str, str]] = []
    for rc in active_routes:
        cond_layer = _conduit_layer_for_category(circuit_cat_map.get(rc.circuit_id))
        route_layer = "E-ROUTE-CONDUIT" if use_homerun_style else cond_layer
        solved_c = circuit_map.get(rc.circuit_id)
        for seg_idx, seg in enumerate(rc.segments):
            x1, y1 = seg.start_mm.x_mm, seg.start_mm.y_mm
            x2, y2 = seg.end_mm.x_mm, seg.end_mm.y_mm
            p1 = (round(x1 / 50.0), round(y1 / 50.0))
            p2 = (round(x2 / 50.0), round(y2 / 50.0))
            key = (p1, p2) if p1 <= p2 else (p2, p1)
            occupants = seg_circuits[key]
            n_occ = len(occupants)
            ox_mm, oy_mm = 0.0, 0.0
            if n_occ > 1:
                slot = occupants.index(rc.circuit_id)
                eff_pitch = min(pitch_mm, (2.0 * max_fan_mm) / max(1, n_occ - 1))
                offset = (slot - (n_occ - 1) / 2.0) * eff_pitch
                ax, ay, bx, by = (x1, y1, x2, y2) if p1 <= p2 else (x2, y2, x1, y1)
                seg_len = math.hypot(bx - ax, by - ay)
                if seg_len > 1e-3:
                    nx, ny = -(by - ay) / seg_len, (bx - ax) / seg_len
                    ox_mm, oy_mm = nx * offset, ny * offset

            sx1, sy1 = _s(x1 + ox_mm), _s(y1 + oy_mm)
            sx2, sy2 = _s(x2 + ox_mm), _s(y2 + oy_mm)
            msp.add_line((sx1, sy1), (sx2, sy2), dxfattribs={"layer": route_layer})

            # In full_routed mode, draw departure arrow right at DB
            if (not use_homerun_style) and seg_idx == 0:
                seg_l = math.hypot(sx2 - sx1, sy2 - sy1)
                if seg_l > _s(250.0):
                    ux, uy = (sx2 - sx1) / seg_l, (sy2 - sy1) / seg_l
                    hx = sx1 + ux * _s(220.0)
                    hy = sy1 + uy * _s(220.0)
                    tick = _s(55.0)
                    msp.add_line(
                        (hx, hy),
                        (hx - ux * tick - uy * tick, hy - uy * tick + ux * tick),
                        dxfattribs={"layer": cond_layer},
                    )
                    msp.add_line(
                        (hx, hy),
                        (hx - ux * tick + uy * tick, hy - uy * tick - ux * tick),
                        dxfattribs={"layer": cond_layer},
                    )
                    if solved_c is not None and discipline != "combined":
                        cores = 5 if solved_c.spec.is_three_phase else 3
                        conduit_d = "25mm" if solved_c.cable_mm2 >= 4.0 else "20mm"
                        callout = f"{rc.circuit_id}:{conduit_d} {cores}x{solved_c.cable_mm2:g}"
                        trunk_callout_anchors.append((hx, hy, callout, cond_layer))

        # Cyprus Consultant Home-Run Drafting: strict per-floor daisy-chain arcs + Home-Run Arrow
        if use_homerun_style:
            c_devs = [devs_by_id_all[did] for did in rc.device_ids if did in devs_by_id_all]
            if discipline == "lighting":
                c_devs = [d for d in c_devs if d.device_type in _LIGHTING_DEVICE_TYPES]
            elif discipline == "power":
                c_devs = [d for d in c_devs if d.device_type in _POWER_DEVICE_TYPES]
            if c_devs:
                devs_by_flr: dict[str, list[PlacedDevice]] = defaultdict(list)
                for cd in c_devs:
                    devs_by_flr[room_floor.get(cd.room_id, "GF")].append(cd)
                for flr, f_devs in devs_by_flr.items():
                    target_bid = board_id_by_floor.get(flr) or (
                        solved_c.spec.board_id if solved_c else db_anchor.id
                    )
                    db_xy = board_locs.get(
                        target_bid,
                        (db_anchor.location_mm.x_mm, db_anchor.location_mm.y_mm),
                    )
                    ordered = sorted(
                        f_devs,
                        key=lambda d: math.hypot(
                            d.location_mm.x_mm - db_xy[0], d.location_mm.y_mm - db_xy[1]
                        ),
                    )
                    if cond_layer != "E-COND-LITE" and len(ordered) >= 2:
                        for a_dev, b_dev in itertools.pairwise(ordered):
                            dist_mm = math.hypot(
                                b_dev.location_mm.x_mm - a_dev.location_mm.x_mm,
                                b_dev.location_mm.y_mm - a_dev.location_mm.y_mm,
                            )
                            if 100.0 <= dist_mm <= 6500.0:
                                _add_dashed_curve(
                                    msp,
                                    (_s(a_dev.location_mm.x_mm), _s(a_dev.location_mm.y_mm)),
                                    (_s(b_dev.location_mm.x_mm), _s(b_dev.location_mm.y_mm)),
                                    layer=cond_layer,
                                    bulge_frac=0.14,
                                    dash_count=7,
                                )
                    first_d = ordered[0]
                    fx, fy = _s(first_d.location_mm.x_mm), _s(first_d.location_mm.y_mm)
                    dx_db = _s(db_xy[0]) - fx
                    dy_db = _s(db_xy[1]) - fy
                    d_len = math.hypot(dx_db, dy_db)
                    ux, uy = (dx_db / d_len, dy_db / d_len) if d_len > 1e-3 else (1.0, 0.0)
                    stub = _s(520.0)
                    hx, hy = fx + ux * stub, fy + uy * stub
                    msp.add_line((fx, fy), (hx, hy), dxfattribs={"layer": cond_layer})
                    tick = _s(90.0)
                    msp.add_line(
                        (hx, hy),
                        (hx - ux * tick - uy * tick * 0.6, hy - uy * tick + ux * tick * 0.6),
                        dxfattribs={"layer": cond_layer},
                    )
                    msp.add_line(
                        (hx, hy),
                        (hx - ux * tick + uy * tick * 0.6, hy - uy * tick - ux * tick * 0.6),
                        dxfattribs={"layer": cond_layer},
                    )
                    if solved_c is not None and discipline != "combined":
                        cores = 5 if solved_c.spec.is_three_phase else 3
                        conduit_d = "25mm" if solved_c.cable_mm2 >= 4.0 else "20mm"
                        callout = (
                            f"->{target_bid} {rc.circuit_id}:{conduit_d} "
                            f"{cores}x{solved_c.cable_mm2:g}"
                        )
                        trunk_callout_anchors.append((hx, hy, callout, cond_layer))
            elif rc.segments:
                s0 = rc.segments[0]
                sx1, sy1 = _s(s0.start_mm.x_mm), _s(s0.start_mm.y_mm)
                sx2, sy2 = _s(s0.end_mm.x_mm), _s(s0.end_mm.y_mm)
                msp.add_line((sx1, sy1), (sx2, sy2), dxfattribs={"layer": cond_layer})

    return room_floor, trunk_callout_anchors


_DXF_STATIC_DEVICE_TAGS: dict[DeviceType, str] = {
    DeviceType.EAC_METER_CABINET: "EAC-MTR H+1500",
    DeviceType.EARTH_PIT: "TT-EARTH-PIT",
    DeviceType.TELECOM_CABINET: "9U-RACK/ONT H+1800",
    DeviceType.INTERCOM_OUTDOOR: "V-INTERCOM-EXT",
    DeviceType.ALARM_PANEL: "ALARM-PANEL",
    DeviceType.ALARM_KEYPAD: "KP H+1400",
    DeviceType.ALARM_SIREN: "SIREN H+3000",
}

_DXF_SHORT_ID_TAG_TEMPLATES: dict[DeviceType, str] = {
    DeviceType.DATA_RJ45: "2xCAT6:{sid}",
    DeviceType.TV_OUTLET: "TV/SAT:{sid}",
    DeviceType.WIFI_AP: "WIFI-AP:{sid}",
    DeviceType.INTERCOM_INDOOR: "V-MON:{sid} H+1500",
    DeviceType.ALARM_PIR: "PIR:{sid}",
    DeviceType.CCTV_CAMERA: "CCTV:{sid} IP67",
    DeviceType.KNX_KEYPAD: "KNX:{sid} H+1200",
    DeviceType.ROOM_THERMOSTAT: "TH:{sid} H+1500",
}

_DXF_LAYER_GROUPS: tuple[tuple[frozenset[DeviceType], str, bool], ...] = (
    (
        frozenset(
            {
                DeviceType.LUMINAIRE,
                DeviceType.PENDANT_LIGHT,
                DeviceType.WALL_SCONCE,
                DeviceType.LINEAR_COVE,
                DeviceType.OUTDOOR_PIR_LIGHT,
                DeviceType.GARDEN_BOLLARD,
                DeviceType.MIRROR_LIGHT,
                DeviceType.EXHAUST_FAN,
            }
        ),
        "E-LITE",
        False,
    ),
    (
        frozenset({DeviceType.SWITCH, DeviceType.DIMMER_SWITCH, DeviceType.FAN_ISOLATOR_3P}),
        "E-SWIT",
        True,
    ),
    (frozenset({DeviceType.SOCKET_TWIN, DeviceType.KITCHEN_FCU}), "E-SOCK", True),
    (
        frozenset(
            {
                DeviceType.SPLIT_AC,
                DeviceType.VRV_OUTDOOR,
                DeviceType.VRV_INDOOR_ISOLATOR,
                DeviceType.HEAT_PUMP_OUTDOOR,
                DeviceType.HEAT_PUMP_HYDROBOX,
                DeviceType.UFH_MANIFOLD,
                DeviceType.ROOM_THERMOSTAT,
            }
        ),
        "E-AC",
        True,
    ),
    (frozenset({DeviceType.EAC_METER_CABINET, DeviceType.EARTH_PIT}), "E-EARTHING", False),
    (
        frozenset(
            {
                DeviceType.TELECOM_CABINET,
                DeviceType.DATA_RJ45,
                DeviceType.TV_OUTLET,
                DeviceType.WIFI_AP,
            }
        ),
        "E-ELV-DATA",
        True,
    ),
    (
        frozenset(
            {
                DeviceType.INTERCOM_OUTDOOR,
                DeviceType.INTERCOM_INDOOR,
                DeviceType.ALARM_PANEL,
                DeviceType.ALARM_PIR,
                DeviceType.ALARM_KEYPAD,
                DeviceType.ALARM_SIREN,
                DeviceType.CCTV_CAMERA,
            }
        ),
        "E-ELV-SEC",
        True,
    ),
    (frozenset({DeviceType.SMOKE_DETECTOR, DeviceType.HEAT_DETECTOR}), "E-ELV-SMOKE", False),
    (frozenset({DeviceType.KNX_KEYPAD}), "E-KNX", True),
)


def _format_dxf_device_tag(dev: PlacedDevice) -> str:
    """Format canonical DXF callout tag for an electrical/ELV placed device."""
    dt = dev.device_type
    if dt in _DXF_STATIC_DEVICE_TAGS:
        return _DXF_STATIC_DEVICE_TAGS[dt]
    short_id = dev.device_id.split("-")[-1]
    if dt in _DXF_SHORT_ID_TAG_TEMPLATES:
        return _DXF_SHORT_ID_TAG_TEMPLATES[dt].format(sid=short_id)

    cid = dev.circuit_id or "?"
    if dt == DeviceType.SMOKE_DETECTOR:
        return f"{dev.circuit_id or 'SD'}:SMOKE"
    if dt == DeviceType.HEAT_DETECTOR:
        return f"{dev.circuit_id or 'HD'}:HEAT"
    if dt == DeviceType.DIMMER_SWITCH:
        return f"{cid}:{short_id} DIM"
    if dt == DeviceType.SHUTTER_SWITCH:
        return f"{cid}:SH H+1200"
    if dt == DeviceType.EV_WALLBOX:
        return f"{dev.circuit_id or 'EV'}:EV-WB H+1300"
    if dt == DeviceType.SWITCH:
        st = dev.switch_type or "1G1W"
        grp = dev.switch_group or "a"
        h_sfx = " H+750" if abs(dev.mounting_height_mm - 750.0) < 10.0 else ""
        return f"{cid}:{short_id} {st}({grp}){h_sfx}"
    if dt == DeviceType.FAN_ISOLATOR_3P:
        return f"{cid}:3P-FAN H+2200"
    if dt in (
        DeviceType.LUMINAIRE,
        DeviceType.PENDANT_LIGHT,
        DeviceType.OUTDOOR_PIR_LIGHT,
        DeviceType.GARDEN_BOLLARD,
    ):
        grp_s = f"({dev.switch_group})" if dev.switch_group else ""
        ip_s = f" {dev.ip_rating}" if dev.ip_rating != "IP20" else ""
        return f"{cid}:{short_id}{grp_s}{ip_s}"
    if dt == DeviceType.MIRROR_LIGHT:
        return f"{cid}:{short_id}(b) IP44 H+2000"
    if dt == DeviceType.EXHAUST_FAN:
        return f"{cid}:FAN(a) H+2400"
    if dt == DeviceType.COOKER_CONTROL_UNIT:
        return f"{cid}:45A-CCU H+1150"
    if dt == DeviceType.WATER_HEATER_DP_SWITCH:
        return f"{cid}:20A-DP H+1200"
    if dt in (DeviceType.SPLIT_AC, DeviceType.VRV_INDOOR_ISOLATOR):
        return f"{cid}:{short_id} H+2200"
    if dt == DeviceType.SOCKET_TWIN and dev.mounting_height_mm >= 1000.0:
        return f"{cid}:{short_id} H+{int(dev.mounting_height_mm)}"
    return f"{cid}:{short_id}"


def _dxf_device_block_layer_rot(dev: PlacedDevice) -> tuple[str, str, float]:
    """Return (block_name, dxf_layer, rotation_deg) for a placed device."""
    blk_name = _BLOCK_NAMES.get(dev.device_type, "CYELEC_APPL")
    if dev.device_type == DeviceType.SWITCH:
        if dev.switch_type == "2G1W":
            blk_name = "CYELEC_SWITCH_2G"
        elif dev.switch_type == "1G2W":
            blk_name = "CYELEC_SWITCH_2W"

    for types, layer, use_wall_angle in _DXF_LAYER_GROUPS:
        if dev.device_type in types:
            rot = (
                dev.wall_angle_deg
                if (use_wall_angle or dev.device_type == DeviceType.MIRROR_LIGHT)
                else 0.0
            )
            return blk_name, layer, rot
    return blk_name, "E-APPL", dev.wall_angle_deg


def _add_elv_interlink_curves(
    msp: Modelspace,
    active_devices: list[PlacedDevice],
    room_floor: dict[str, str],
    inv_scale: float,
) -> None:
    """Draw per-floor interlinked smoke/heat detector and KNX bus dashed arcs on E-103."""
    def _s(v_mm: float) -> float:
        return round(v_mm * inv_scale, 4)

    for types, layer, bulge in (
        (frozenset({DeviceType.SMOKE_DETECTOR, DeviceType.HEAT_DETECTOR}), "E-ELV-SMOKE", 0.14),
        (frozenset({DeviceType.KNX_KEYPAD}), "E-KNX", -0.14),
    ):
        by_flr: dict[str, list[PlacedDevice]] = defaultdict(list)
        for d in active_devices:
            if d.device_type in types:
                by_flr[room_floor.get(d.room_id, "GF")].append(d)
        for flr_devs in by_flr.values():
            for a_d, b_d in itertools.pairwise(flr_devs):
                if (
                    math.hypot(
                        b_d.location_mm.x_mm - a_d.location_mm.x_mm,
                        b_d.location_mm.y_mm - a_d.location_mm.y_mm,
                    )
                    <= 8500.0
                ):
                    _add_dashed_curve(
                        msp,
                        (_s(a_d.location_mm.x_mm), _s(a_d.location_mm.y_mm)),
                        (_s(b_d.location_mm.x_mm), _s(b_d.location_mm.y_mm)),
                        layer=layer,
                        bulge_frac=bulge,
                        dash_count=7,
                    )


def _add_dxf_device_blocks(
    msp: Modelspace,
    spatial: SpatialLayoutResult,
    inv_scale: float,
    discipline: LayoutDiscipline,
    room_floor: dict[str, str],
    occupied_boxes: list[tuple[float, float, float, float]],
    trunk_callout_anchors: list[tuple[float, float, str, str]],
) -> None:
    """Insert wall-rotated IEC 60617 / ELV blocks, switch arcs & de-collided circuit tags."""
    def _s(v_mm: float) -> float:
        return round(v_mm * inv_scale, 4)

    if discipline == "lighting":
        active_devices = [d for d in spatial.devices if d.device_type in _LIGHTING_DEVICE_TYPES]
    elif discipline == "power":
        active_devices = [d for d in spatial.devices if d.device_type in _POWER_DEVICE_TYPES]
    elif discipline == "elv":
        active_devices = [d for d in spatial.devices if d.device_type in _ELV_DEVICE_TYPES]
    else:
        active_devices = [
            d
            for d in spatial.devices
            if d.device_type in (_LIGHTING_DEVICE_TYPES | _POWER_DEVICE_TYPES)
        ]

    for dev in active_devices:
        x, y = _s(dev.location_mm.x_mm), _s(dev.location_mm.y_mm)
        rad = _s(125.0)
        occupied_boxes.append((x - rad, y - rad, x + rad, y + rad))

    for dev in active_devices:
        x, y = _s(dev.location_mm.x_mm), _s(dev.location_mm.y_mm)
        tag = _format_dxf_device_tag(dev)
        blk_name, layer, rot = _dxf_device_block_layer_rot(dev)
        msp.add_blockref(blk_name, (x, y), dxfattribs={"layer": layer, "rotation": rot})
        lx, ly = _place_decollided_label(x, y, tag, _s(60.0), occupied_boxes, inv_scale)
        msp.add_text(tag, dxfattribs={"layer": layer, "height": _s(60.0)}).set_placement((lx, ly))

    if discipline in ("combined", "lighting") and spatial.switch_legs:
        dev_by_id = {d.device_id: d for d in active_devices}
        for leg in spatial.switch_legs:
            sw_dev = dev_by_id.get(leg.switch_id)
            if sw_dev is None:
                continue
            p_sw = (_s(sw_dev.location_mm.x_mm), _s(sw_dev.location_mm.y_mm))
            for idx, tid in enumerate(leg.target_device_ids):
                t_dev = dev_by_id.get(tid)
                if t_dev is None:
                    continue
                if room_floor.get(t_dev.room_id, "GF") != room_floor.get(sw_dev.room_id, "GF"):
                    continue
                p_tgt = (_s(t_dev.location_mm.x_mm), _s(t_dev.location_mm.y_mm))
                bulge = 0.16 if idx % 2 == 0 else -0.16
                _add_dashed_curve(msp, p_sw, p_tgt, layer="E-SWIT-LEG", bulge_frac=bulge)

    if discipline == "elv":
        _add_elv_interlink_curves(msp, active_devices, room_floor, inv_scale)

    for cx, cy, callout, cond_layer in trunk_callout_anchors:
        lx, ly = _place_decollided_label(cx, cy, callout, _s(55.0), occupied_boxes, inv_scale)
        msp.add_text(callout, dxfattribs={"layer": cond_layer, "height": _s(55.0)}).set_placement(
            (lx, ly)
        )



def _add_dxf_border_and_paper_space(
    doc: Drawing,
    msp: Modelspace,
    result: Phase2DesignResult,
    inv_scale: float,
    discipline: LayoutDiscipline,
    preserved_underlay: bool,
) -> None:
    """Add synthetic fallback border (when no architect underlay) & true-scale ISO sheets."""
    def _s(v_mm: float) -> float:
        return round(v_mm * inv_scale, 4)

    # 4. Model-space border ONLY for synthetic fallback (never overwrite real architect DXFs!)
    if not preserved_underlay:
        all_pts = [p for r in result.design.building.rooms for p in r.polygon_mm]
        if all_pts:
            minx = _s(min(p.x_mm for p in all_pts) - 800.0)
            maxx = _s(max(p.x_mm for p in all_pts) + 800.0)
            miny = _s(min(p.y_mm for p in all_pts) - 800.0)
            maxy = _s(max(p.y_mm for p in all_pts) + 800.0)
            msp.add_lwpolyline(
                [(minx, miny), (maxx, miny), (maxx, maxy), (minx, maxy)],
                close=True,
                dxfattribs={"layer": "E-TITLE"},
            )

    # 5. Exact True-Scale ISO Paper-Space Sheet(s) with Graphical Legend, Scale Bar & North Arrow
    all_rooms = result.design.building.rooms
    base_dwg_no = (
        "E-101"
        if discipline == "lighting"
        else ("E-102" if discipline == "power" else ("E-103" if discipline == "elv" else "E-100"))
    )
    _add_iso_paper_space_sheet(
        doc,
        result,
        rooms_subset=all_rooms,
        inv_scale=inv_scale,
        floor_tag=None,
        dwg_no=base_dwg_no,
        discipline=discipline,
    )

    # For multi-floor buildings, also create one dedicated true-scale print sheet per floor!
    sb_order = [sb.floor for sb in result.design.building.sub_boards]
    distinct_floors = list(dict.fromkeys([*sb_order, *(r.floor for r in all_rooms)]))
    if len(distinct_floors) >= 2:
        for f_idx, flr in enumerate(distinct_floors, start=1):
            flr_rooms = [r for r in all_rooms if r.floor == flr]
            _add_iso_paper_space_sheet(
                doc,
                result,
                rooms_subset=flr_rooms,
                inv_scale=inv_scale,
                floor_tag=flr,
                dwg_no=f"{base_dwg_no}.{f_idx}",
                discipline=discipline,
            )



def render_electrical_layout_dxf(
    result: Phase2DesignResult,
    *,
    source_dxf_path: Path | None = None,
    discipline: LayoutDiscipline = "combined",
    force_clean_r2018: bool = False,
) -> Drawing:
    """Overlay IEC 60617 electrical blocks, offset conduits & true-scale ISO sheets onto DXF."""
    orig_path = _resolve_original_dxf_path(result, source_dxf_path)
    preserved_underlay = False
    inv_scale = 1.0

    mf_doc = _build_multifloor_underlay_doc(result) if orig_path is None else None
    if mf_doc is not None:
        doc = mf_doc
        preserved_underlay = True
        inv_scale = 1.0
    elif orig_path is not None:
        try:
            raw_doc = _read_dxf_safe(orig_path)
            needs_clone = force_clean_r2018 or (raw_doc.dxfversion != "AC1032")
            if not needs_clone:
                try:
                    needs_clone = len(raw_doc.audit().errors) > 0
                except Exception:
                    needs_clone = True
            doc = _clone_underlay_to_r2018(raw_doc) if needs_clone else raw_doc
            preserved_underlay = True
            unit_scale = result.design.building.source_unit_scale_to_mm
            inv_scale = 1.0 / unit_scale if unit_scale > 0 else 1.0
        except (OSError, DXFError):
            doc = render_building_to_dxf(result.design.building)
    else:
        doc = render_building_to_dxf(result.design.building)

    def _s(v_mm: float) -> float:
        return v_mm * inv_scale

    for layer_name, color_idx in {**ELECTRICAL_LAYERS, **STAGE5_EXTRA_LAYERS}.items():
        if layer_name not in doc.layers:
            doc.layers.add(layer_name, color=color_idx)

    _ensure_iec_blocks(doc, inv_scale)
    msp = doc.modelspace()
    spatial = result.spatial

    # Mark DB1 / MDB and Sub-DB anchors cleanly with named CYELEC_DB block
    db_anchor = result.design.building.db_anchor
    db_anchors = [(db_anchor.id, db_anchor.location_mm)]
    for sb in result.design.building.sub_boards:
        db_anchors.append((sb.board_id, sb.location_mm))

    occupied_boxes: list[tuple[float, float, float, float]] = []
    # Seed occupied_boxes with existing architectural TEXT/MTEXT so E-* labels never overlap them
    for ent in msp:
        etype = ent.dxftype()
        if etype not in ("TEXT", "MTEXT"):
            continue
        try:
            ins = ent.dxf.insert
            ex, ey = float(ins.x), float(ins.y)
            if etype == "TEXT":
                txt = str(ent.dxf.text or "")
                th = max(float(getattr(ent.dxf, "height", 0.0) or _s(80.0)), _s(40.0))
            else:
                txt = str(ent.plain_text() if hasattr(ent, "plain_text") else "")
                th = max(float(getattr(ent.dxf, "char_height", 0.0) or _s(80.0)), _s(40.0))
            first_ln = txt.splitlines()[0] if txt else ""
            if not first_ln:
                continue
            tw = max(th * 0.65 * min(len(first_ln), 32), th * 2.0)
            n_lines = max(1, min(len(txt.splitlines()), 3))
            pad_a = _s(20.0)
            occupied_boxes.append(
                (ex - pad_a, ey - pad_a, ex + tw + pad_a, ey + th * 1.25 * n_lines + pad_a)
            )
        except Exception:
            _logger.debug("Skipping underlay text entity during label de-collision scan.")
            continue

    for db_id, db_pt in db_anchors:
        dx, dy = _s(db_pt.x_mm), _s(db_pt.y_mm)
        msp.add_blockref("CYELEC_DB", (dx, dy), dxfattribs={"layer": "E-TITLE"})
        occupied_boxes.append((dx - _s(240.0), dy - _s(130.0), dx + _s(240.0), dy + _s(130.0)))
        lbl_x, lbl_y = _place_decollided_label(dx, dy, db_id, _s(110.0), occupied_boxes, inv_scale)
        msp.add_text(db_id, dxfattribs={"layer": "E-TITLE", "height": _s(110.0)}).set_placement(
            (lbl_x, lbl_y)
        )

    _add_dxf_wet_zones(msp, spatial, inv_scale, discipline)
    room_floor, trunk_callout_anchors = _add_dxf_conduit_routes(
        doc, msp, result, inv_scale, discipline
    )
    _add_dxf_device_blocks(
        msp, spatial, inv_scale, discipline, room_floor, occupied_boxes, trunk_callout_anchors
    )
    _add_dxf_border_and_paper_space(
        doc, msp, result, inv_scale, discipline, preserved_underlay
    )
    return doc


def _select_true_scale_sheet(
    span_x_mm: float,
    span_y_mm: float,
) -> tuple[str, float, float, int, float]:
    """Select the smallest ISO A3/A1 sheet & standard metric scale fitting at 100% EXACT scale."""
    # (sheet_label, sheet_w_mm, sheet_h_mm, scale_denom, title_strip_w_mm)
    candidates: list[tuple[str, float, float, int, float]] = [
        ("ISO A3", 420.0, 297.0, 50, 92.0),
        ("ISO A3", 420.0, 297.0, 100, 92.0),
        ("ISO A1", 841.0, 594.0, 50, 130.0),
        ("ISO A1", 841.0, 594.0, 100, 130.0),
        ("ISO A1", 841.0, 594.0, 200, 130.0),
    ]
    margin = 10.0
    for label, sw, sh, denom, strip_w in candidates:
        vp_w = (sw - 2.0 * margin) - strip_w - 6.0
        vp_h = (sh - 2.0 * margin) - 8.0
        if span_x_mm * 1.08 <= vp_w * denom and span_y_mm * 1.08 <= vp_h * denom:
            return label, sw, sh, denom, strip_w
    return candidates[-1]


def _add_iso_paper_space_sheet(
    doc: Drawing,
    result: Phase2DesignResult,
    *,
    rooms_subset: Sequence[Room],
    inv_scale: float = 1.0,
    floor_tag: str | None = None,
    dwg_no: str = "E-101",
    discipline: LayoutDiscipline = "combined",
) -> None:
    """Create a true-scale ISO A3/A1 Paper-Space tab with right title strip, legend & VIEWPORT."""
    from cyelec.models.building import Room

    rooms = [r for r in rooms_subset if isinstance(r, Room)]
    all_pts = [p for r in rooms for p in r.polygon_mm]
    if not all_pts:
        return

    raw_minx = min(p.x_mm for p in all_pts)
    raw_maxx = max(p.x_mm for p in all_pts)
    raw_miny = min(p.y_mm for p in all_pts)
    raw_maxy = max(p.y_mm for p in all_pts)
    span_x_mm = max(1000.0, raw_maxx - raw_minx)
    span_y_mm = max(1000.0, raw_maxy - raw_miny)

    sheet_label, sheet_w, sheet_h, scale_denom, strip_w = _select_true_scale_sheet(
        span_x_mm, span_y_mm
    )
    size_tag = "A3" if sheet_w < 500.0 else "A1"
    disc_pfx = (
        "E101-LITE"
        if discipline == "lighting"
        else (
            "E102-PWR" if discipline == "power" else ("E103-ELV" if discipline == "elv" else "EAC")
        )
    )
    tab_prefix = f"{disc_pfx}-{floor_tag}" if floor_tag else disc_pfx
    tab_name = f"{tab_prefix}-{size_tag}-1-{scale_denom}"

    if tab_name in doc.layouts:
        doc.layouts.delete(tab_name)
    psp = doc.layouts.new(tab_name)
    psp.page_setup(
        size=(sheet_w, sheet_h),
        margins=(10.0, 10.0, 10.0, 10.0),
        units="mm",
    )

    margin = 10.0
    inner_x0, inner_y0 = margin, margin
    inner_x1, inner_y1 = sheet_w - margin, sheet_h - margin

    # Outer paper boundary & inner printable border (Paper-Space mm)
    psp.add_lwpolyline(
        [(0.0, 0.0), (sheet_w, 0.0), (sheet_w, sheet_h), (0.0, sheet_h)],
        close=True,
        dxfattribs={"layer": "E-TITLE"},
    )
    psp.add_lwpolyline(
        [
            (inner_x0, inner_y0),
            (inner_x1, inner_y0),
            (inner_x1, inner_y1),
            (inner_x0, inner_y1),
        ],
        close=True,
        dxfattribs={"layer": "E-TITLE"},
    )

    # Right-side Cyprus EAC / ETEK Title Strip + Graphical IEC 60617 Legend
    strip_x0 = inner_x1 - strip_w
    psp.add_line((strip_x0, inner_y0), (strip_x0, inner_y1), dxfattribs={"layer": "E-TITLE"})

    # Graphical IEC 60617 Legend (upper right panel)
    leg_top = inner_y1 - 8.0
    psp.add_text(
        "IEC 60617 SYMBOL LEGEND", dxfattribs={"layer": "E-TITLE", "height": 3.0}
    ).set_placement((strip_x0 + 4.0, leg_top))
    psp.add_line(
        (strip_x0, leg_top - 3.0), (inner_x1, leg_top - 3.0), dxfattribs={"layer": "E-TITLE"}
    )

    if discipline == "lighting":
        sym_items = [
            ("E-LITE", "Luminaire / Pendant / PIR", "lum"),
            ("E-SWIT", "Switch 1G/2G/2W/Dim (+1.2m)", "sw"),
            ("E-SWIT", "3P Fan Isolator (+2.2m)", "appl"),
            ("E-SWIT-LEG", "Switch Control Leg (a/b)", "line"),
            ("E-COND-LITE", "Lighting Home-Run Arrow", "line"),
            ("E-TITLE", "Distribution Board (+1.6m)", "box"),
        ]
    elif discipline == "power":
        sym_items = [
            ("E-SOCK", "Twin 13A Socket / Kitchen FCU", "sock"),
            ("E-AC", "A/C / VRV / Heat Pump / Thermo", "ac"),
            ("E-APPL", "Shutters / CCU / EV / Pump", "appl"),
            ("E-COND-PWR", "Power Loop & Home-Run", "line"),
            ("E-COND-DED", "Dedicated Heavy Feeder", "line"),
            ("E-EARTHING", "EAC Meter / TT Earth Pit", "box"),
            ("E-WETZONE-EXCL", "EAC 2.5m/3.0m Socket Excl", "box"),
        ]
    elif discipline == "elv":
        sym_items = [
            ("E-ELV-DATA", "9U Rack / 2xCat6 / TV / Wi-Fi", "box"),
            ("E-ELV-SEC", "Video Intercom (Out / In)", "appl"),
            ("E-ELV-SEC", "Alarm Panel / PIR / Keypad", "sw"),
            ("E-ELV-SEC", "IP67 PoE CCTV Camera", "ac"),
            ("E-ELV-SMOKE", "BS 5839-6 Smoke / Heat Det", "lum"),
            ("E-KNX", "KNX Scene Keypad & Bus", "line"),
        ]
    else:
        sym_items = [
            ("E-LITE", "Luminaire / Fan / Mirror", "lum"),
            ("E-SWIT", "Switch 1G/2G/2W + Dimmer", "sw"),
            ("E-SOCK", "Twin 13A Socket (+0.45/1.15m)", "sock"),
            ("E-AC", "HVAC / A/C / Heat Pump", "ac"),
            ("E-APPL", "Shutters / CCU / EV / Plant", "appl"),
            ("E-COND-LITE", "Lighting Home-Run & Legs", "line"),
            ("E-COND-PWR", "Power Loops & Home-Runs", "line"),
            ("E-COND-DED", "Dedicated Feeder", "line"),
            ("E-EARTHING", "DB / EAC Meter / Earth Pit", "box"),
        ]
    row_step = 11.0 if size_tag == "A3" else 14.0
    for idx, (lyr, desc, kind) in enumerate(sym_items):
        cy = leg_top - 10.0 - idx * row_step
        sx = strip_x0 + 9.0
        if kind == "lum":
            psp.add_circle((sx, cy), radius=2.6, dxfattribs={"layer": lyr})
            psp.add_line((sx - 1.8, cy - 1.8), (sx + 1.8, cy + 1.8), dxfattribs={"layer": lyr})
            psp.add_line((sx - 1.8, cy + 1.8), (sx + 1.8, cy - 1.8), dxfattribs={"layer": lyr})
        elif kind == "sw":
            psp.add_circle((sx, cy), radius=1.6, dxfattribs={"layer": lyr})
            psp.add_line((sx, cy), (sx + 3.0, cy + 3.0), dxfattribs={"layer": lyr})
        elif kind == "sock":
            psp.add_arc(
                (sx, cy - 1.0),
                radius=2.4,
                start_angle=0.0,
                end_angle=180.0,
                dxfattribs={"layer": lyr},
            )
            psp.add_line((sx - 2.4, cy - 1.0), (sx + 2.4, cy - 1.0), dxfattribs={"layer": lyr})
        elif kind == "ac":
            psp.add_lwpolyline(
                [
                    (sx - 3.2, cy - 1.8),
                    (sx + 3.2, cy - 1.8),
                    (sx + 3.2, cy + 1.8),
                    (sx - 3.2, cy + 1.8),
                ],
                close=True,
                dxfattribs={"layer": lyr},
            )
            psp.add_line((sx - 3.2, cy - 1.8), (sx + 3.2, cy + 1.8), dxfattribs={"layer": lyr})
        elif kind == "appl":
            psp.add_lwpolyline(
                [
                    (sx - 2.4, cy - 2.4),
                    (sx + 2.4, cy - 2.4),
                    (sx + 2.4, cy + 2.4),
                    (sx - 2.4, cy + 2.4),
                ],
                close=True,
                dxfattribs={"layer": lyr},
            )
        elif kind == "line":
            psp.add_line((sx - 4.0, cy), (sx + 4.0, cy), dxfattribs={"layer": lyr})
        else:
            psp.add_lwpolyline(
                [
                    (sx - 3.2, cy - 2.0),
                    (sx + 3.2, cy - 2.0),
                    (sx + 3.2, cy + 2.0),
                    (sx - 3.2, cy + 2.0),
                ],
                close=True,
                dxfattribs={"layer": lyr},
            )
        psp.add_text(desc, dxfattribs={"layer": "E-TITLE", "height": 2.4}).set_placement(
            (strip_x0 + 16.0, cy - 1.1)
        )

    # Bottom-Right Cyprus EAC / ETEK Title Block inside right strip
    tb_top = inner_y0 + (98.0 if size_tag == "A3" else 132.0)

    eac = result.design.eac_compliance
    br = result.design.branding
    meta = result.design.eac_metadata
    firm = br.firm_name if br and br.firm_name else "ETEK LICENSED PRACTICE"
    eng = br.engineer_name if br and br.engineer_name else "ETEK REVIEW PENDING"
    reg = br.etek_reg_no if br and br.etek_reg_no else "ETEK-E"
    floor_lbl = f" ({floor_tag})" if floor_tag else ""
    disc_title = (
        "LIGHTING & SWITCHING"
        if discipline == "lighting"
        else (
            "POWER & MECHANICAL PLANT"
            if discipline == "power"
            else ("ELV, SECURITY & LIFE SAFETY" if discipline == "elv" else "EAC / ETEK LAYOUT")
        )
    )
    earth_txt = (
        f"EARTH: {result.design.earthing_system.value} ({result.design.ze_or_ra_ohm:.1f} OHM)"
    )
    plot_txt = (
        f"PLOT: {meta.district} {meta.sheet_plan}/{meta.plot_no}".strip()
        if meta and (meta.district or meta.plot_no)
        else "STD H+: SW=1.2m SK=0.45m KT=1.15m"
    )
    # (text, nominal height, gap-after) - every field is printed in FULL (wrapped). The
    # block keeps its classic 10 mm row rhythm when everything fits on one line each and
    # otherwise tightens / steps the text down, finally growing upward (arrow & scale bar
    # follow tb_top) - nothing is ever truncated.
    tb_fields: list[tuple[str, float, float]] = [
        (f"{disc_title}{floor_lbl}", 2.7, 9.0),
        (f"{sheet_label} | SCALE 1:{scale_denom} (METRIC)", 2.2, 10.0),
        (f"PROJ: {result.design.building.project_name}", 2.0, 10.0),
        (f"SUPPLY: {eac.recommended_supply_tier}", 2.0, 10.0),
        (earth_txt, 2.0, 10.0),
        (plot_txt, 1.9, 10.0),
        (f"FIRM: {firm}", 2.0, 10.0),
        (f"ENG: {eng}", 2.0, 9.0),
        (f"REG: {reg} | {dwg_no}", 2.0, 8.0),
        (f"SHA: {result.design.provenance.input_sha256[:16]} | SIGN-OFF", 1.8, 0.0),
    ]
    tb_text_w = strip_w - 7.0
    tb_avail = tb_top - inner_y0 - 3.0
    scale_k = 1.0
    while True:
        tb_lines = [
            (wrap_text_chars(txt, dxf_char_budget(tb_text_w, h * scale_k)), h * scale_k, gap)
            for txt, h, gap in tb_fields
        ]
        extra = sum((len(ls) - 1) * h * 1.45 for ls, h, _g in tb_lines)
        need = 9.0 + sum(g for _l, _h, g in tb_lines) + extra
        if need <= tb_avail or scale_k <= 0.75:
            break
        scale_k = round(scale_k - 0.05, 2)
    if need > tb_avail:
        tb_top += need - tb_avail
    psp.add_line((strip_x0, tb_top), (inner_x1, tb_top), dxfattribs={"layer": "E-TITLE"})
    ty = tb_top - 9.0
    for ls, h, gap in tb_lines:
        for k, seg in enumerate(ls):
            if k:
                ty -= h * 1.45
            psp.add_text(seg, dxfattribs={"layer": "E-TITLE", "height": h}).set_placement(
                (strip_x0 + 3.5, ty)
            )
        ty -= gap

    # North Arrow inside right strip above title block
    nx, ny = strip_x0 + strip_w * 0.5, tb_top + 16.0
    psp.add_circle((nx, ny), radius=6.5, dxfattribs={"layer": "E-TITLE"})
    psp.add_lwpolyline(
        [(nx, ny + 6.5), (nx - 2.8, ny - 4.5), (nx, ny - 2.0), (nx + 2.8, ny - 4.5)],
        close=True,
        dxfattribs={"layer": "E-TITLE"},
    )
    psp.add_text("N", dxfattribs={"layer": "E-TITLE", "height": 3.2}).set_placement(
        (nx - 1.3, ny + 8.0)
    )

    # Metric Scale Bar inside right strip above North Arrow (0 .. 1m .. 2m .. 5m at EXACT 1:scale)
    sb_y0 = tb_top + 34.0
    mm_per_m = 1000.0 / float(scale_denom)
    max_m = 3 if (5.0 * mm_per_m) > (strip_w - 14.0) else 5
    bar_len = float(max_m) * mm_per_m
    sb_x0 = strip_x0 + (strip_w - bar_len) * 0.5
    psp.add_lwpolyline(
        [
            (sb_x0, sb_y0),
            (sb_x0 + bar_len, sb_y0),
            (sb_x0 + bar_len, sb_y0 + 2.8),
            (sb_x0, sb_y0 + 2.8),
        ],
        close=True,
        dxfattribs={"layer": "E-TITLE"},
    )
    ticks = (0, 1, 2, 3) if max_m == 3 else (0, 1, 2, 5)
    for m_tick in ticks:
        tx = sb_x0 + m_tick * mm_per_m
        psp.add_line((tx, sb_y0), (tx, sb_y0 + 4.2), dxfattribs={"layer": "E-TITLE"})
        psp.add_text(f"{m_tick}m", dxfattribs={"layer": "E-TITLE", "height": 2.2}).set_placement(
            (tx - 1.4, sb_y0 + 5.2)
        )
    psp.add_text(
        f"SCALE 1:{scale_denom} (METRIC)", dxfattribs={"layer": "E-TITLE", "height": 2.4}
    ).set_placement((sb_x0, sb_y0 - 4.8))

    # EXACT True-Scale DXF Paper-Space VIEWPORT (zero silent scale distortion!)
    vp_w = (strip_x0 - inner_x0) - 6.0
    vp_h = (inner_y1 - inner_y0) - 8.0
    vp_cx = inner_x0 + 3.0 + (vp_w / 2.0)
    vp_cy = inner_y0 + 4.0 + (vp_h / 2.0)
    model_cx = ((raw_minx + raw_maxx) / 2.0) * inv_scale
    model_cy = ((raw_miny + raw_maxy) / 2.0) * inv_scale
    view_height_model = (vp_h * float(scale_denom)) * inv_scale
    psp.add_viewport(
        center=(vp_cx, vp_cy),
        size=(vp_w, vp_h),
        view_center_point=(model_cx, model_cy),
        view_height=view_height_model,
        dxfattribs={"layer": "E-TITLE"},
    )


def export_electrical_layout_dxf(
    result: Phase2DesignResult,
    out_path: Path,
    *,
    source_dxf_path: Path | None = None,
    discipline: LayoutDiscipline = "combined",
) -> Path:
    """Write stampable Phase 2 electrical overlay R2018 DXF with IEC blocks & true-scale sheets."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        doc = render_electrical_layout_dxf(
            result,
            source_dxf_path=source_dxf_path,
            discipline=discipline,
        )
        doc.saveas(str(out_path))
    except Exception:
        doc = render_electrical_layout_dxf(
            result,
            source_dxf_path=source_dxf_path,
            discipline=discipline,
            force_clean_r2018=True,
        )
        doc.saveas(str(out_path))
    return out_path


def export_discipline_layout_dxfs(
    result: Phase2DesignResult,
    out_dir: Path,
    *,
    source_dxf_path: Path | None = None,
) -> dict[str, Path]:
    """Export E-101 Lighting, E-102 Power, and combined coordination electrical_layout.dxf."""
    out_dir.mkdir(parents=True, exist_ok=True)
    return {
        "e101_lighting_dxf": export_electrical_layout_dxf(
            result,
            out_dir / "E101_lighting_layout.dxf",
            source_dxf_path=source_dxf_path,
            discipline="lighting",
        ),
        "e102_power_dxf": export_electrical_layout_dxf(
            result,
            out_dir / "E102_power_layout.dxf",
            source_dxf_path=source_dxf_path,
            discipline="power",
        ),
        "layout_dxf": export_electrical_layout_dxf(
            result,
            out_dir / "electrical_layout.dxf",
            source_dxf_path=source_dxf_path,
            discipline="combined",
        ),
    }
