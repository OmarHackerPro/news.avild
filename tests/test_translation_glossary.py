from app.api.translation_glossary import apply_glossary


class TestApplyGlossary:
    def test_russian_apt_replacement(self):
        assert apply_glossary("Группа APT атакует банки.", "ru") == "Группа АПТ атакует банки."

    def test_word_boundary_does_not_match_substring(self):
        # APTITUDE contains "APT" but should not be rewritten.
        assert apply_glossary("APTITUDE и APT", "ru") == "APTITUDE и АПТ"

    def test_replaces_all_occurrences(self):
        assert apply_glossary("APT и APT снова", "ru") == "АПТ и АПТ снова"

    def test_other_language_unchanged(self):
        assert apply_glossary("Le groupe APT attaque", "fr") == "Le groupe APT attaque"

    def test_unknown_language_unchanged(self):
        assert apply_glossary("APT", "xx") == "APT"

    def test_empty_string(self):
        assert apply_glossary("", "ru") == ""
