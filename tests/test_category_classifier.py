import pytest
from unittest.mock import AsyncMock, patch
from app.ingestion.category_classifier import CategoryDecision, classify_categories


class TestCategoryDecision:
    def test_dataclass_fields(self):
        d = CategoryDecision(
            label="ransomware",
            ingest=True,
            priority_modifier=0.2,
            notes="High-signal cybersecurity category",
        )
        assert d.label == "ransomware"
        assert d.ingest is True
        assert d.priority_modifier == 0.2
        assert d.notes == "High-signal cybersecurity category"


class TestClassifyCategories:
    @pytest.mark.anyio
    async def test_returns_list_of_decisions(self):
        mock_response = [
            {"label": "ransomware", "ingest": True, "priority_modifier": 0.2, "notes": "high signal"},
            {"label": "sponsored", "ingest": False, "priority_modifier": 0.0, "notes": "paid content"},
        ]
        with patch(
            "app.ingestion.category_classifier._call_llm",
            new_callable=AsyncMock,
            return_value=mock_response,
        ):
            result = await classify_categories("TestFeed", ["ransomware", "sponsored"])

        assert len(result) == 2
        assert result[0].label == "ransomware"
        assert result[0].ingest is True
        assert result[1].label == "sponsored"
        assert result[1].ingest is False

    @pytest.mark.anyio
    async def test_returns_allow_all_on_llm_failure(self):
        """If LLM call fails, every label defaults to ingest=True, modifier=0."""
        with patch(
            "app.ingestion.category_classifier._call_llm",
            new_callable=AsyncMock,
            side_effect=Exception("LLM unavailable"),
        ):
            result = await classify_categories("TestFeed", ["ransomware", "news"])

        assert all(d.ingest is True for d in result)
        assert all(d.priority_modifier == 0.0 for d in result)

    @pytest.mark.anyio
    async def test_empty_labels_returns_empty_list(self):
        result = await classify_categories("TestFeed", [])
        assert result == []
