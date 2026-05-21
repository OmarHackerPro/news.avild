from app.ingestion.scorer import compute_cluster_score


class TestCredibilityFactor:
    """Source credibility adds 0-15 pts based on max_credibility_weight."""

    def _base_kwargs(self, **overrides) -> dict:
        defaults = {
            "article_count": 1,
            "max_cvss": None,
            "cve_count": 0,
            "entity_keys": [],
            "state": "new",
            "latest_at": "2026-04-23T00:00:00+00:00",
            "max_credibility_weight": 1.0,
        }
        defaults.update(overrides)
        return defaults

    def test_high_credibility_source_scores_15(self):
        result = compute_cluster_score(**self._base_kwargs(max_credibility_weight=1.5))
        cred = next(f for f in result["top_factors"] if f["factor"] == "source_credibility")
        assert cred["points"] == 15.0

    def test_medium_credibility_scores_10(self):
        result = compute_cluster_score(**self._base_kwargs(max_credibility_weight=1.2))
        cred = next(f for f in result["top_factors"] if f["factor"] == "source_credibility")
        assert cred["points"] == 10.0

    def test_default_credibility_scores_5(self):
        result = compute_cluster_score(**self._base_kwargs(max_credibility_weight=1.0))
        cred = next(f for f in result["top_factors"] if f["factor"] == "source_credibility")
        assert cred["points"] == 5.0

    def test_low_credibility_scores_0(self):
        result = compute_cluster_score(**self._base_kwargs(max_credibility_weight=0.5))
        cred = next(f for f in result["top_factors"] if f["factor"] == "source_credibility")
        assert cred["points"] == 0.0

    def test_defaults_to_5_when_param_omitted(self):
        """max_credibility_weight has a default of 1.0 so existing callers are unaffected."""
        result = compute_cluster_score(
            article_count=1,
            max_cvss=None,
            cve_count=0,
            entity_keys=[],
            state="new",
            latest_at="2026-04-23T00:00:00+00:00",
        )
        cred = next(f for f in result["top_factors"] if f["factor"] == "source_credibility")
        assert cred["points"] == 5.0

    def test_score_capped_at_100(self):
        """Max possible score (all factors maxed) must not exceed 100."""
        result = compute_cluster_score(
            article_count=10,
            max_cvss=10.0,
            cve_count=5,
            entity_keys=["e1", "e2", "e3", "e4", "e5"],
            state="confirmed",
            latest_at="2026-04-23T00:00:00+00:00",
            max_credibility_weight=1.5,
        )
        assert result["score"] <= 100.0

    def test_credibility_factor_in_top_factors(self):
        result = compute_cluster_score(**self._base_kwargs(max_credibility_weight=1.5))
        factor_names = [f["factor"] for f in result["top_factors"]]
        assert "source_credibility" in factor_names


class TestEpssFactor:
    """EPSS adds 0-15 pts scaled linearly on the raw exploitation probability."""

    def _base_kwargs(self, **overrides) -> dict:
        defaults = {
            "article_count": 1,
            "max_cvss": None,
            "cve_count": 0,
            "entity_keys": [],
            "state": "new",
            "latest_at": "2026-05-21T00:00:00+00:00",
            "max_credibility_weight": 1.0,
        }
        defaults.update(overrides)
        return defaults

    def test_epss_scaled_linearly_at_15_pts(self):
        result = compute_cluster_score(**self._base_kwargs(max_epss=0.62))
        epss = next(f for f in result["top_factors"] if f["factor"] == "epss")
        assert epss["points"] == 9.3  # round(0.62 * 15, 1)

    def test_epss_label_shows_percentage(self):
        result = compute_cluster_score(**self._base_kwargs(max_epss=0.62))
        epss = next(f for f in result["top_factors"] if f["factor"] == "epss")
        assert epss["label"] == "EPSS 62% exploit probability"

    def test_epss_none_produces_no_factor(self):
        result = compute_cluster_score(**self._base_kwargs(max_epss=None))
        assert all(f["factor"] != "epss" for f in result["top_factors"])

    def test_epss_zero_produces_no_factor(self):
        result = compute_cluster_score(**self._base_kwargs(max_epss=0.0))
        assert all(f["factor"] != "epss" for f in result["top_factors"])

    def test_epss_omitted_param_produces_no_factor(self):
        """max_epss defaults to None so existing callers are unaffected."""
        result = compute_cluster_score(**self._base_kwargs())
        assert all(f["factor"] != "epss" for f in result["top_factors"])

    def test_score_capped_at_100_with_epss(self):
        result = compute_cluster_score(
            article_count=10,
            max_cvss=10.0,
            cve_count=5,
            entity_keys=["e1", "e2", "e3", "e4", "e5"],
            state="confirmed",
            latest_at="2026-05-21T00:00:00+00:00",
            max_credibility_weight=1.5,
            cisa_kev=True,
            max_epss=1.0,
        )
        assert result["score"] <= 100.0
