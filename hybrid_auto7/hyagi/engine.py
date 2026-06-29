from math import isfinite

from .config import inch_to_m
from .model import build_wires
from .physics import FreqResult, swr_from_impedance


class NecppEngine:
    def __init__(self):
        try:
            import necpp
        except Exception as exc:
            raise RuntimeError(f"Could not import necpp: {exc}") from exc

        self.necpp = necpp

    def evaluate(self, elements, ant, freqs_mhz):
        if not freqs_mhz:
            return []

        results = []
        for f in freqs_mhz:
            results.append(self.evaluate_one(elements, ant, f))
        return results

    def _apply_ground(self, ctx, ant):
        n = self.necpp
        mode = getattr(ant, "ground_mode", "average")

        if mode == "free_space":
            return

        if not hasattr(n, "nec_gn_card"):
            return

        func = n.nec_gn_card

        if mode == "perfect":
            func(ctx, 1, 0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
            return

        presets = {
            "poor": (5.0, 0.001),
            "average": (13.0, 0.005),
            "good": (20.0, 0.03),
            "custom": (
                float(getattr(ant, "ground_epsr", 13.0)),
                float(getattr(ant, "ground_sigma_s_per_m", 0.005)),
            ),
        }

        if mode not in presets:
            mode = "average"

        epsr, sigma = presets[mode]

        # 9-argument necpp form:
        # ctx, ground_type, nradials, epsr, sigma, a, b, c, d
        func(ctx, 2, 0, float(epsr), float(sigma), 0.0, 0.0, 0.0, 0.0)

    def evaluate_one(self, elements, ant, freq_mhz):
        n = self.necpp
        ctx = n.nec_create()

        if ctx is None:
            raise RuntimeError("necpp failed to create NEC context")

        try:
            wires, feed_tag, feed_seg = build_wires(elements, ant)

            for w in wires:
                n.nec_wire(
                    ctx,
                    int(w.tag),
                    int(w.segments),
                    float(inch_to_m(w.x1_in)),
                    float(inch_to_m(w.y1_in)),
                    float(inch_to_m(w.z1_in)),
                    float(inch_to_m(w.x2_in)),
                    float(inch_to_m(w.y2_in)),
                    float(inch_to_m(w.z2_in)),
                    float(inch_to_m(w.radius_in)),
                    1.0,
                    1.0,
                )

            n.nec_geometry_complete(ctx, 0)
            self._apply_ground(ctx, ant)
            n.nec_fr_card(ctx, 0, 1, float(freq_mhz), 0.0)

            n.nec_ex_card(
                ctx,
                0,
                int(feed_tag),
                int(feed_seg),
                0,
                1.0,
                0.0,
                0.0,
                0.0,
                0.0,
                0.0,
            )

            n.nec_xq_card(ctx, 0)

            r = float(self._impedance_real(ctx))
            x = float(self._impedance_imag(ctx))

            if not isfinite(r) or not isfinite(x):
                raise RuntimeError(f"Non-finite impedance returned at {freq_mhz} MHz: R={r}, X={x}")

            swr = swr_from_impedance(r, x)

            return FreqResult(
                freq_mhz=freq_mhz,
                r_ohm=r,
                x_ohm=x,
                swr_50=swr,
            )

        finally:
            try:
                n.nec_delete(ctx)
            except Exception:
                pass

    def _impedance_real(self, ctx):
        func = self.necpp.nec_impedance_real

        try:
            return func(ctx, 0)
        except TypeError:
            return func(ctx)

    def _impedance_imag(self, ctx):
        func = self.necpp.nec_impedance_imag

        try:
            return func(ctx, 0)
        except TypeError:
            return func(ctx)
