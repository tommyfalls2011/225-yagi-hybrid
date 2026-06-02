"""Diagnostic: dump everything necpp exposes."""
import inspect
import necpp

print("=== necpp version / build ===")
print("module file:", necpp.__file__)

print("\n=== All callable names containing 'gain' or 'rp' ===")
for name in sorted(dir(necpp)):
    if not name.startswith("_") and any(k in name.lower() for k in ("gain", "rp", "radiation", "pattern")):
        obj = getattr(necpp, name)
        if callable(obj):
            try:
                sig = inspect.signature(obj)
                print(f"  {name}{sig}")
            except (ValueError, TypeError):
                # SWIG-generated funcs often don't expose signatures
                print(f"  {name}(<no introspection — SWIG>)")

print("\n=== Live test: build a simple dipole, try rp+gain ===")
nec = necpp.nec_create()
necpp.nec_wire(nec, 1, 21, -2.5, 0, 10, 2.5, 0, 10, 0.005, 1.0, 1.0)
necpp.nec_geometry_complete(nec, 0)
necpp.nec_gn_card(nec, -1, 0, 0, 0, 0, 0, 0, 0)  # free space
necpp.nec_fr_card(nec, 0, 1, 27.195, 0)
necpp.nec_ex_card(nec, 0, 1, 11, 0, 1.0, 0.0, 0, 0, 0, 0)

# rp_card attempt — 14 args total per necpp ABI
print("\nTrying nec_rp_card(nec, 0, 1, 1, 0, 0, 0, 0, 90.0, 90.0, 0, 0, 0, 0):")
try:
    r = necpp.nec_rp_card(nec, 0, 1, 1, 0, 0, 0, 0, 90.0, 90.0, 0, 0, 0, 0)
    print(f"  -> rc={r}")
except Exception as e:
    print(f"  -> EXCEPTION: {type(e).__name__}: {e}")

print("\nTrying nec_xq_card(nec, 0):")
try:
    r = necpp.nec_xq_card(nec, 0)
    print(f"  -> rc={r}")
except Exception as e:
    print(f"  -> EXCEPTION: {type(e).__name__}: {e}")

print("\nTrying nec_gain(nec, 0, 0, 0):")
try:
    g = necpp.nec_gain(nec, 0, 0, 0)
    print(f"  -> gain = {g} dB")
except Exception as e:
    print(f"  -> EXCEPTION: {type(e).__name__}: {e}")

# Try a few alternate arg counts in case this build is patched
for args in [(nec,), (nec, 0), (nec, 0, 0)]:
    try:
        g = necpp.nec_gain(*args)
        print(f"  nec_gain{args[1:]} -> {g}")
    except Exception as e:
        print(f"  nec_gain{args[1:]} -> {type(e).__name__}: {e}")

necpp.nec_delete(nec)
