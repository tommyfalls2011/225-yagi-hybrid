import sys

sys.path.insert(0, "/work/hybrid_27mhz_yagi")

from hyagi.config import AntennaConfig, SweepConfig
from hyagi.geometry import build_geometry, build_tapered_wires, validate_geometry
from hyagi.nec_writer import generate_nec_text

ant = AntennaConfig()
sweep = SweepConfig()

elements = build_geometry(
    ant_cfg=ant,
    de_position_in=61,
    xfrmr_spacing_in=12,
    coupler_spacing_in=12,
    xfrmr_len_in=209,
    coupler_len_in=197,
    de_len_in=203,
)

validate_geometry(elements, ant)

wires, feed_tag, feed_seg = build_tapered_wires(elements, ant)

print("elements:", len(elements))
print("wires:", len(wires))
print("feed_tag:", feed_tag)
print("feed_seg:", feed_seg)

nec = generate_nec_text(elements, ant, sweep)

print("nec lines:", len(nec.splitlines()))
print("first NEC lines:")
for line in nec.splitlines()[:10]:
    print(line)

print("OK")
