"""OpenEMS verification exporter for hybrid_auto7 geometries.

Builds a FDTD model of the antenna and runs it for a single frequency-band
sweep.  OpenEMS gives volumetric Method-of-Moments-free accuracy that's
materially better than nec2c for closely-coupled wires like the hybrid
driven cell -- a hybrid's XFRMR/DE/COUPLER are 12-18\" apart, well within
nec2c's degraded-accuracy zone for parallel wires.

OpenEMS is SLOW compared to nec2c (FDTD has to mesh the whole simulation
volume): a 27 MHz 7-element Yagi takes 5-20 minutes per run on a 12-thread
machine vs ~50ms in nec2c.  So this is a VERIFICATION pass on a finished
tune, NOT something you can put in the optimizer loop.

Usage:
    from hyagi.openems_export import build_simulation, run_simulation
    sim_dir, port = build_simulation(elements, height_ft=22.0, fc=27.195,
                                     bandwidth=4.0, output_dir='/tmp/sim')
    swr_curve, gain, fb = run_simulation(sim_dir, port, fc, bandwidth)
"""
from __future__ import annotations

import os
import shutil
import sys
import pathlib
from typing import List, Dict, Tuple

# OpenEMS python bindings live in the system dist-packages, not the venv.
_OEMS_PATH = "/usr/lib/python3/dist-packages"
if _OEMS_PATH not in sys.path:
    sys.path.insert(0, _OEMS_PATH)

import numpy as np                              # noqa: E402

# Lazy-import openEMS/CSXCAD only when actually needed -- they pull in heavy
# C++ libs we don't want loaded just by importing hyagi.
def _import_openems():
    from openEMS import openEMS                 # noqa: E501
    from openEMS.physical_constants import C0
    from CSXCAD import ContinuousStructure
    return openEMS, ContinuousStructure, C0


INCH = 0.0254                                   # metres per inch
FT = 0.3048                                     # metres per foot


def build_simulation(elements: List[Dict], *, height_ft: float,
                     fc_mhz: float, bandwidth_mhz: float = 4.0,
                     output_dir: str = "/tmp/openems_hybrid",
                     mesh_per_wavelength: int = 25,
                     wire_radius_in: float = 0.3125,    # 0.625" OD / 2
                     ground_type: str = "real",
                     numthreads: int = 0):
    """Build an OpenEMS FDTD simulation of the antenna.

    elements   : list of dicts with name / position_in / length_in.
    height_ft  : antenna height above ground.
    fc_mhz     : design centre frequency.
    bandwidth_mhz : width of the Gaussian excitation pulse (default 4 MHz).
    output_dir : where to put the run files (cleared if it exists).
    mesh_per_wavelength : λ/N cell size; 25 = decent for verification.
    ground_type : 'real' (lossy soil), 'pec' (perfect conductor), or 'none'.
    numthreads : 0 = use all available cores.

    Returns (sim_dir, port, fc_hz).  Call run_simulation(...) to execute it.
    """
    openEMS, CSXCAD, C0 = _import_openems()

    # Wipe + recreate output dir.
    out = pathlib.Path(output_dir)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    fc_hz = float(fc_mhz) * 1e6
    bw_hz = float(bandwidth_mhz) * 1e6
    wavelength = C0 / fc_hz                     # in metres
    max_cell = wavelength / mesh_per_wavelength

    # Total simulation volume: ~1.5 wavelengths beyond the antenna in every
    # direction (PML boundary takes ~8 cells; need open space around).
    # Antenna spans:
    #   X (boom): 0 to max(position_in)
    #   Y (element span): -max_half_len to +max_half_len
    #   Z (height): height_ft
    max_pos = max(float(e["position_in"]) for e in elements) * INCH
    max_half = max(float(e["length_in"]) for e in elements) * INCH / 2.0
    height_m = float(height_ft) * FT

    pad = 1.5 * wavelength
    x_min, x_max = -pad, max_pos + pad
    y_min, y_max = -max_half - pad, max_half + pad
    z_min = 0.0 if ground_type != "none" else height_m - 2 * pad
    z_max = height_m + pad

    # FDTD instance
    F = openEMS(NrTS=30000, EndCriteria=1e-4)
    if numthreads:
        F.SetNumberOfThreads(numthreads)
    F.SetGaussExcite(fc_hz, bw_hz / 2.0)        # Gaussian centred on fc

    # PML on all sides, except the bottom where the ground plane (if any) sits.
    if ground_type in ("real", "pec"):
        F.SetBoundaryCond(["PML_8", "PML_8", "PML_8", "PML_8", "PML_8", "MUR"])
    else:
        F.SetBoundaryCond(["PML_8"] * 6)

    # Geometry
    CSX = CSXCAD()
    F.SetCSX(CSX)
    mesh = CSX.GetGrid()
    mesh.SetDeltaUnit(1.0)                      # metres

    metal = CSX.AddMetal("Element")
    wire_r = float(wire_radius_in) * INCH

    de_x = None
    for el in elements:
        x = float(el["position_in"]) * INCH
        half = float(el["length_in"]) * INCH / 2.0
        # Each element as a thin cylinder along the Y axis, at height_m.
        start = [x, -half, height_m]
        stop  = [x,  half, height_m]
        metal.AddCylinder(start=start, stop=stop, radius=wire_r,
                          priority=10)
        if str(el["name"]).upper() == "DE":
            de_x = x

    if de_x is None:
        raise ValueError("No DE element in the geometry; can't add feed port.")

    # Feed: lumped port at the DE centre.  Small gap perpendicular to the
    # element axis -- OpenEMS expects a thin port region.
    port_half = max(wire_r * 1.5, max_cell)
    port_start = [de_x, -port_half, height_m]
    port_stop  = [de_x,  port_half, height_m]
    port = F.AddLumpedPort(port_nr=1, R=50.0,
                           start=port_start, stop=port_stop,
                           p_dir='y', excite=1.0)

    # Add a real-ground plane (lossy soil) if requested.
    if ground_type == "real":
        soil = CSX.AddMaterial("soil", epsilon=13.0, kappa=0.005)
        soil_bottom = z_min
        soil_top = soil_bottom + 0.5            # 50 cm soil layer suffices
        soil.AddBox(start=[x_min, y_min, soil_bottom],
                    stop=[x_max, y_max, soil_top], priority=1)
    elif ground_type == "pec":
        # PEC ground plane just below the simulation domain
        ground = CSX.AddMetal("ground")
        ground.AddBox(start=[x_min, y_min, 0.0],
                      stop=[x_max, y_max, 0.0], priority=1)

    # Mesh: enforce lambda/N cells everywhere, with finer mesh in the
    # antenna region (especially across the driven cell where wire-wire
    # coupling matters).
    mesh.AddLine('x', [x_min, x_max])
    mesh.AddLine('y', [y_min, y_max])
    mesh.AddLine('z', [z_min, z_max])
    # Force samples at each element x-coord (capture coupling exactly)
    for el in elements:
        x = float(el["position_in"]) * INCH
        mesh.AddLine('x', [x - 0.04, x, x + 0.04])
    # Force samples at the element tip y-coords
    for el in elements:
        half = float(el["length_in"]) * INCH / 2.0
        mesh.AddLine('y', [-half, half])
    mesh.AddLine('z', [height_m - 0.04, height_m, height_m + 0.04])
    mesh.SmoothMeshLines('all', max_cell, ratio=1.4)

    # Far-field NF2FF box for pattern computation.
    nf2ff = F.CreateNF2FFBox()

    return F, CSX, port, nf2ff, str(out), fc_hz, bw_hz


def run_simulation(sim_obj, *, frequencies=None, theta_steps=37, phi_steps=73,
                   verbose=True) -> Dict:
    """Run the already-built simulation and read S11 + far-field.

    sim_obj : the tuple returned by build_simulation().
    frequencies : np.array of frequencies (Hz) to evaluate S11 at.  If None,
                  31 points across the excitation bandwidth.
    Returns dict:
        freqs_hz, s11_db, swr, R_centre, X_centre, gain_dbi, fb_db.
    """
    F, CSX, port, nf2ff, sim_dir, fc_hz, bw_hz = sim_obj

    if frequencies is None:
        frequencies = np.linspace(fc_hz - bw_hz / 2, fc_hz + bw_hz / 2, 31)

    if verbose:
        print(f"[openEMS] running {len(frequencies)}-point sweep around "
              f"{fc_hz/1e6:.3f} MHz... this can take 5-20 minutes")
    F.Run(sim_dir, verbose=2 if verbose else 0, cleanup=True)

    port.CalcPort(sim_dir, frequencies)
    s11 = port.uf_ref / port.uf_inc
    Z = port.uf_tot / port.if_tot
    swr = (1.0 + np.abs(s11)) / np.maximum(1.0 - np.abs(s11), 1e-9)
    fc_idx = int(np.argmin(np.abs(frequencies - fc_hz)))
    R_centre = float(np.real(Z[fc_idx]))
    X_centre = float(np.imag(Z[fc_idx]))

    # Far field at fc
    theta = np.arange(0, 181, 5) * np.pi / 180
    phi = np.arange(0, 360, 5) * np.pi / 180
    ff = nf2ff.CalcNF2FF(sim_dir, np.array([fc_hz]), theta, phi, verbose=0)
    e_norm = ff.E_norm[0]                      # shape (theta, phi)
    Pmax = e_norm.max()
    peak_idx = np.unravel_index(np.argmax(e_norm), e_norm.shape)
    pk_theta, pk_phi = theta[peak_idx[0]], phi[peak_idx[1]]
    gain_dbi = 10.0 * np.log10(Pmax / np.mean(e_norm**2 * np.sin(theta[:, None]))
                               if (e_norm**2).mean() > 0 else 1.0)
    # F/B: gain at (peak_theta, peak_phi + 180 deg), bilinear-interp not
    # needed here because OpenEMS gives us the requested grid exactly.
    back_phi_idx = (peak_idx[1] + (180 // 5)) % len(phi)
    back_gain = 20.0 * np.log10(max(e_norm[peak_idx[0], back_phi_idx], 1e-9))
    peak_gain = 20.0 * np.log10(max(Pmax, 1e-9))
    fb_db = float(peak_gain - back_gain)

    return {
        "freqs_hz": frequencies.tolist(),
        "swr": [float(s) for s in swr],
        "s11_db": [20.0 * float(np.log10(max(abs(x), 1e-9))) for x in s11],
        "R_centre": R_centre, "X_centre": X_centre,
        "centre_swr": float(swr[fc_idx]),
        "gain_dbi": float(gain_dbi),
        "fb_db": fb_db,
        "peak_elev_deg": 90.0 - float(np.degrees(pk_theta)),
        "peak_az_deg": float(np.degrees(pk_phi)),
    }
