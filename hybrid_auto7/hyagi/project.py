from dataclasses import dataclass, asdict, field
import json

from .paths import DATA_DIR, ensure_dirs


PROJECTS_DIR = DATA_DIR / "projects"


@dataclass
class ProjectConfig:
    name: str

    element_count: int = 7
    mode: str = "hybrid"

    tuning_procedure: str = "legacy_hybrid"

    # new
    design_priority: str = "balanced"   # wideband, balanced, high_gain
    tx_power_watts: float = 100.0

    freq_start_mhz: float = 26.965
    freq_stop_mhz: float = 27.405
    target_z_ohm: float = 50.0

    height_ft: float = 36.0
    boom_length_ft: float = 30.0
    boom_diameter_in: float = 2.0

    center_od_in: float = 0.625
    outer_od_in: float = 0.500
    center_half_len_in: float = 36.0

    ground_mode: str = "average"
    ground_epsr: float = 13.0
    ground_sigma_s_per_m: float = 0.005

    cell_mounting_style: str = "full_cell_insulated"

    target_max_swr: float = 1.5
    min_front_back_db: float = 20.0
    prefer_gain: bool = True

    champion_run_id: int | None = None
    element_overrides: dict[str, dict[str, float]] = field(default_factory=dict)

    notes: str = ""


def _sanitize_project_name(name: str) -> str:
    safe = str(name).replace("/", "_").replace("\\", "_").strip()
    if not safe:
        raise ValueError("project name cannot be empty")
    return safe


def validate_project(cfg: ProjectConfig) -> bool:
    if not (3 <= cfg.element_count <= 12):
        raise ValueError("element_count must be between 3 and 12")

    if cfg.mode not in ("hybrid", "yagi"):
        raise ValueError("mode must be 'hybrid' or 'yagi'")

    if cfg.tuning_procedure not in ("legacy_hybrid", "wide_cell_owa", "cell_then_directors_repeat"):
        raise ValueError("invalid tuning_procedure")

    if cfg.design_priority not in ("wideband", "balanced", "high_gain"):
        raise ValueError("invalid design_priority")

    if cfg.tx_power_watts <= 0:
        raise ValueError("tx_power_watts must be positive")

    if cfg.cell_mounting_style not in ("full_cell_insulated", "de_only_insulated"):
        raise ValueError("invalid cell_mounting_style")

    if cfg.freq_start_mhz <= 0 or cfg.freq_stop_mhz <= 0:
        raise ValueError("frequencies must be positive")

    if cfg.freq_stop_mhz <= cfg.freq_start_mhz:
        raise ValueError("freq_stop_mhz must be greater than freq_start_mhz")

    if cfg.target_z_ohm <= 0:
        raise ValueError("target_z_ohm must be positive")

    if cfg.height_ft <= 0:
        raise ValueError("height_ft must be positive")

    if cfg.boom_length_ft <= 0:
        raise ValueError("boom_length_ft must be positive")

    if cfg.boom_diameter_in <= 0:
        raise ValueError("boom_diameter_in must be positive")

    if cfg.center_od_in <= 0 or cfg.outer_od_in <= 0:
        raise ValueError("element diameters must be positive")

    if cfg.center_half_len_in <= 0:
        raise ValueError("center_half_len_in must be positive")

    if cfg.target_max_swr < 1.0:
        raise ValueError("target_max_swr must be at least 1.0")

    if cfg.min_front_back_db < 0:
        raise ValueError("min_front_back_db must be non-negative")

    if cfg.champion_run_id is not None and int(cfg.champion_run_id) <= 0:
        raise ValueError("champion_run_id must be positive when provided")

    if not isinstance(cfg.element_overrides, dict):
        raise ValueError("element_overrides must be a dictionary")

    valid_ground_modes = {"free_space", "perfect", "average", "good", "poor", "custom"}
    if cfg.ground_mode not in valid_ground_modes:
        raise ValueError("invalid ground_mode")

    if cfg.ground_epsr <= 0:
        raise ValueError("ground_epsr must be positive")

    if cfg.ground_sigma_s_per_m < 0:
        raise ValueError("ground_sigma_s_per_m must be non-negative")

    return True


def project_path(name: str):
    ensure_dirs()
    PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
    safe = _sanitize_project_name(name)
    return PROJECTS_DIR / f"{safe}.json"


def save_project(cfg: ProjectConfig):
    validate_project(cfg)
    path = project_path(cfg.name)
    path.write_text(json.dumps(asdict(cfg), indent=2, sort_keys=True), encoding="utf-8")
    return path


def load_project(name: str) -> ProjectConfig:
    path = project_path(name)

    if not path.exists():
        raise FileNotFoundError(f"Project not found: {name}")

    data = json.loads(path.read_text(encoding="utf-8"))
    data.setdefault("element_overrides", {})
    data.setdefault("prefer_gain", True)
    data.setdefault("champion_run_id", None)
    data.setdefault("notes", "")
    data.setdefault("ground_mode", "average")
    data.setdefault("ground_epsr", 13.0)
    data.setdefault("ground_sigma_s_per_m", 0.005)
    data.setdefault("tuning_procedure", "legacy_hybrid")
    data.setdefault("cell_mounting_style", "full_cell_insulated")
    data.setdefault("design_priority", "balanced")
    data.setdefault("tx_power_watts", 100.0)

    cfg = ProjectConfig(**data)
    validate_project(cfg)
    return cfg


def list_projects() -> list[ProjectConfig]:
    ensure_dirs()
    PROJECTS_DIR.mkdir(parents=True, exist_ok=True)

    out: list[ProjectConfig] = []

    for p in sorted(PROJECTS_DIR.glob("*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            data.setdefault("element_overrides", {})
            data.setdefault("prefer_gain", True)
            data.setdefault("champion_run_id", None)
            data.setdefault("notes", "")
            data.setdefault("ground_mode", "average")
            data.setdefault("ground_epsr", 13.0)
            data.setdefault("ground_sigma_s_per_m", 0.005)
            data.setdefault("tuning_procedure", "legacy_hybrid")
            data.setdefault("cell_mounting_style", "full_cell_insulated")
            data.setdefault("design_priority", "balanced")
            data.setdefault("tx_power_watts", 100.0)
            out.append(ProjectConfig(**data))
        except Exception:
            continue

    return out


def set_champion(project_name: str, run_id: int):
    cfg = load_project(project_name)
    cfg.champion_run_id = int(run_id)
    return save_project(cfg)


def print_project(cfg: ProjectConfig):
    print()
    print(f"Project: {cfg.name}")
    print("=" * (9 + len(cfg.name)))
    print()
    print("Antenna")
    print("-------")
    print(f"Elements:             {cfg.element_count}")
    print(f"Mode:                 {cfg.mode}")
    print(f"Tuning procedure:     {cfg.tuning_procedure}")
    print(f"Design priority:      {cfg.design_priority}")
    print(f"TX power:             {cfg.tx_power_watts:.1f} W")
    print(f"Cell mounting style:  {cfg.cell_mounting_style}")
    print()
    print("Frequency")
    print("---------")
    print(f"Start:                {cfg.freq_start_mhz:.6f} MHz")
    print(f"Stop:                 {cfg.freq_stop_mhz:.6f} MHz")
    print(f"Target impedance:     {cfg.target_z_ohm:.1f} ohms")
    print()
    print("Mechanical")
    print("----------")
    print(f"Height:               {cfg.height_ft:.3f} ft")
    print(f"Boom length:          {cfg.boom_length_ft:.3f} ft")
    print(f"Boom diameter:        {cfg.boom_diameter_in:.3f} in")
    print()
    print("Element taper")
    print("-------------")
    print(f"Center OD:            {cfg.center_od_in:.3f} in")
    print(f"Outer OD:             {cfg.outer_od_in:.3f} in")
    print(f"Center half length:   {cfg.center_half_len_in:.3f} in")
    print()
    print("Ground")
    print("------")
    print(f"Ground mode:          {cfg.ground_mode}")
    if cfg.ground_mode == "custom":
        print(f"Ground epsr:          {cfg.ground_epsr:.3f}")
        print(f"Ground sigma:         {cfg.ground_sigma_s_per_m:.6f} S/m")
    print()
    print("Optimization")
    print("------------")
    print(f"Target max SWR:       {cfg.target_max_swr:.3f}")
    print(f"Minimum F/B:          {cfg.min_front_back_db:.3f} dB")
    print(f"Prefer gain:          {cfg.prefer_gain}")
    print()
    print("Champion")
    print("--------")
    print(f"Champion run id:      {cfg.champion_run_id}")

    print()
    print("Overrides")
    print("---------")
    if not cfg.element_overrides:
        print("None")
    else:
        for name in sorted(cfg.element_overrides):
            ov = cfg.element_overrides[name]
            pos = ov.get("position_in")
            length = ov.get("length_in")
            print(f"{name}: position_in={pos} length_in={length}")

    if cfg.notes:
        print()
        print("Notes")
        print("-----")
        print(cfg.notes)


def set_element_override(project_name: str, element_name: str, position_in=None, length_in=None):
    cfg = load_project(project_name)

    if position_in is None and length_in is None:
        raise ValueError("Nothing to set. Use position_in and/or length_in.")

    name = str(element_name).upper().strip()
    if not name:
        raise ValueError("element_name cannot be empty")

    current = dict(cfg.element_overrides.get(name, {}))

    if position_in is not None:
        current["position_in"] = float(position_in)

    if length_in is not None:
        length_in = float(length_in)
        if length_in <= 0:
            raise ValueError("length_in must be positive")
        current["length_in"] = length_in

    cfg.element_overrides[name] = current
    return save_project(cfg)


def apply_element_overrides(cfg: ProjectConfig, elements):
    if not cfg.element_overrides:
        return elements

    for e in elements:
        ov = cfg.element_overrides.get(str(e.name).upper())
        if not ov:
            continue

        if "position_in" in ov:
            e.position_in = float(ov["position_in"])

        if "length_in" in ov:
            e.length_in = float(ov["length_in"])

    return elements
