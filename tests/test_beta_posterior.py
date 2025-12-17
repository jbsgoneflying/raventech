from backend.stats_utils import beta_posterior_from_counts


def test_beta_posterior_mean_and_ci_uniform_prior():
    # Beta(1,1) prior with no data: mean=0.5 and 90% CI is exactly [0.05, 0.95].
    post = beta_posterior_from_counts(successes=0, trials=0, alpha0=1.0, beta0=1.0)
    assert post is not None
    assert abs(post.mean - 0.5) < 1e-12
    lo, hi = post.ci(level=0.90)
    assert abs(lo - 0.05) < 1e-6
    assert abs(hi - 0.95) < 1e-6


def test_beta_posterior_shrinks_small_samples():
    # 0/10 breaches with uniform prior => Beta(1,11) posterior mean = 1/12.
    post = beta_posterior_from_counts(successes=0, trials=10, alpha0=1.0, beta0=1.0)
    assert post is not None
    assert abs(post.mean - (1.0 / 12.0)) < 1e-9
    lo, hi = post.ci(level=0.90)
    assert 0.0 <= lo < post.mean < hi <= 1.0


