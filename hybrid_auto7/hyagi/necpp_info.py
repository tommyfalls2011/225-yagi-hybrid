def show_necpp_info():
    try:
        import necpp
    except Exception as exc:
        print("Could not import necpp:")
        print(repr(exc))
        return

    print("necpp module imported OK")
    print("========================")
    print()

    names = sorted(dir(necpp))

    nec_names = [n for n in names if n.startswith("nec")]
    rp_names = [n for n in names if "rp" in n.lower()]
    gain_names = [n for n in names if "gain" in n.lower()]
    pattern_names = [
        n for n in names
        if "pattern" in n.lower()
        or "radiat" in n.lower()
        or "power" in n.lower()
        or "eff" in n.lower()
    ]

    print("All nec* functions")
    print("------------------")
    for n in nec_names:
        print(n)

    print()
    print("Possible RP / radiation pattern functions")
    print("-----------------------------------------")
    for n in rp_names:
        print(n)

    print()
    print("Possible gain functions")
    print("-----------------------")
    for n in gain_names:
        print(n)

    print()
    print("Possible power/efficiency/pattern functions")
    print("-------------------------------------------")
    for n in pattern_names:
        print(n)

    print()
    print("Function docstrings for likely functions")
    print("----------------------------------------")

    likely = [
        "nec_rp_card",
        "nec_gain",
        "nec_gain_max",
        "nec_gain_mean",
        "nec_gain_min",
        "nec_radiation_pattern",
        "nec_power_budget",
        "nec_input_power",
        "nec_radiated_power",
    ]

    for name in likely:
        if hasattr(necpp, name):
            obj = getattr(necpp, name)
            print()
            print(name)
            print("-" * len(name))
            print(getattr(obj, "__doc__", None))
