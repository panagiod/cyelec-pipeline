"""Architectural DXF/IFC ingestion and deterministic EN/EL room classification."""

from cyelec.ingest.dxf_ingest import ingest_dxf, ingest_multi_floor_dxf, inspect_dxf_layers
from cyelec.ingest.ifc_ingest import (
    IfcFixtureRecord,
    IfcSpaceRecord,
    build_model_from_ifc_records,
    ingest_ifc,
)
from cyelec.ingest.room_classifier import (
    RoomClassificationResult,
    RoomClassifierFallback,
    classify_room_label,
    clean_room_label,
)

__all__ = [
    "IfcFixtureRecord",
    "IfcSpaceRecord",
    "RoomClassificationResult",
    "RoomClassifierFallback",
    "build_model_from_ifc_records",
    "classify_room_label",
    "clean_room_label",
    "ingest_dxf",
    "ingest_ifc",
    "ingest_multi_floor_dxf",
    "inspect_dxf_layers",
]
