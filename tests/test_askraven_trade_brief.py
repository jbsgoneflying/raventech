from backend.askraven import build_trade_brief


def test_trade_brief_derives_spot_and_strike_distances_engine2():
    ctx = {
        "engine": "engine2",
        "liveContext": {"dealerGamma": {"spot": 6923.0, "netGammaSign": "positive", "magnitudeBucket": "low"}},
        "current": {"regime": {"score100": 28.4, "bucket": "MODERATE"}, "macro": {"multiplier": 1.36}},
        "technicals": {"enabled": True, "ema": {"ema21": 6900.0}},
    }
    q = "I have the 6950/6955c spread expiring today"
    tb = build_trade_brief(question=q, context_pack=ctx)
    assert tb["engine"] == "engine2"
    assert tb["spot"] == 6923.0
    assert tb["parsedTrade"]["strikes"][:2] == [6950, 6955]
    d0 = tb["strikeDistances"][0]
    assert d0["strike"] == 6950
    assert round(d0["pts"], 1) == 27.0


