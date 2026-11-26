"""License-isolated subprocess .dwg -> .dxf bridge (ODAFileConverter / dwg2dxf)."""

from __future__ import annotations

import shutil
import subprocess  # nosec B404
import tempfile
from pathlib import Path

from cyelec.models.electrical import ReviewFlag, ReviewSeverity


def find_dwg_converter() -> tuple[str, str] | None:
    """Locate an installed external DWG-to-DXF CLI converter without linking GPL libraries."""
    for tool in ("ODAFileConverter", "dwg2dxf"):
        exe = shutil.which(tool)
        if exe:
            return tool, exe
    return None


def is_dwg_converter_available() -> bool:
    """Return True if an isolated external DWG-to-DXF CLI converter is available on PATH."""
    return find_dwg_converter() is not None


def convert_dwg_to_dxf(
    dwg_path: Path,
    out_dir: Path,
    *,
    timeout_s: float = 60.0,
) -> tuple[Path, ReviewFlag]:
    """Convert a binary AutoCAD .dwg file to ASCII R2018 .dxf via an isolated subprocess.

    Supports ODAFileConverter (preferred) or LibreDWG's dwg2dxf CLI strictly over process
    boundaries (zero GPLv3 library linking, shell=False, bounded timeout).
    """
    if not dwg_path.is_file():
        raise ValueError(f"DWG file not found: '{dwg_path}'.")

    found = find_dwg_converter()
    if found is None:
        raise ValueError(
            "Native .dwg conversion requires an external converter CLI ('ODAFileConverter' or "
            "'dwg2dxf') on PATH. Export as AutoCAD R2018 ASCII .dxf (or .ifc) or install "
            "ODAFileConverter on the worker host."
        )

    tool, exe = found
    out_dir.mkdir(parents=True, exist_ok=True)
    target_dxf = out_dir / f"{dwg_path.stem}.dxf"

    if tool == "ODAFileConverter":
        with tempfile.TemporaryDirectory(prefix="cyelec_oda_in_") as in_tmp:
            in_dir = Path(in_tmp)
            staged = in_dir / dwg_path.name
            shutil.copy2(dwg_path, staged)
            cmd = [
                exe,
                str(in_dir),
                str(out_dir),
                "ACAD2018",
                "DXF",
                "0",
                "1",
                dwg_path.name,
            ]
            proc = subprocess.run(  # nosec B603
                cmd,
                shell=False,
                capture_output=True,
                text=True,
                timeout=timeout_s,
                check=False,
            )
    else:
        cmd = [exe, "-y", "-o", str(target_dxf), str(dwg_path)]
        proc = subprocess.run(  # nosec B603
            cmd,
            shell=False,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )

    if not target_dxf.is_file() or target_dxf.stat().st_size == 0:
        # Check if ODA wrote uppercase .DXF or alternative casing
        candidates = [p for p in out_dir.iterdir() if p.is_file() and p.suffix.lower() == ".dxf"]
        if candidates:
            target_dxf = candidates[0]
        else:
            err_tail = (proc.stderr or proc.stdout or "").strip()[-240:]
            raise ValueError(
                f"DWG-to-DXF conversion via '{tool}' failed (exit {proc.returncode})"
                + (f": {err_tail}" if err_tail else ".")
            )

    flag = ReviewFlag(
        code="DWG_CONVERTED_VIA_SUBPROCESS",
        severity=ReviewSeverity.INFO,
        category="geometry",
        message=(
            f"Converted native AutoCAD '{dwg_path.name}' to R2018 DXF via isolated "
            f"subprocess '{tool}'. Verify converted layers and scale on floorplan overlay."
        ),
        requires_etek_signoff=False,
    )
    return target_dxf, flag
