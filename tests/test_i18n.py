import json
import tempfile
import unittest
from pathlib import Path

from mangoo.i18n import Catalog, LanguageCatalog, _parse_accept_language, catalog_from_settings, resolve_language, translator_for
from mangoo.request import Request
from mangoo.templates import render_template


def _write_catalog(directory: Path, language: str, data: dict) -> None:
    (directory / f"{language}.json").write_text(json.dumps(data), encoding="utf-8")


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self._tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tempdir.cleanup)
        self.directory = Path(self._tempdir.name)

    def test_translates_a_plain_string(self):
        _write_catalog(self.directory, "en", {"welcome": "Welcome!"})
        catalog = Catalog(self.directory, languages=("en",), default_language="en")
        self.assertEqual(catalog.translate("welcome", "en"), "Welcome!")

    def test_interpolates_keyword_arguments(self):
        _write_catalog(self.directory, "en", {"greeting": "Hello, {name}!"})
        catalog = Catalog(self.directory, languages=("en",), default_language="en")
        self.assertEqual(catalog.translate("greeting", "en", name="Alice"), "Hello, Alice!")

    def test_missing_key_falls_back_to_the_key_itself(self):
        _write_catalog(self.directory, "en", {})
        catalog = Catalog(self.directory, languages=("en",), default_language="en")
        self.assertEqual(catalog.translate("no_such_key", "en"), "no_such_key")

    def test_missing_language_file_falls_back_to_default_language(self):
        _write_catalog(self.directory, "en", {"welcome": "Welcome!"})
        catalog = Catalog(self.directory, languages=("en", "es"), default_language="en")
        self.assertEqual(catalog.translate("welcome", "es"), "Welcome!")

    def test_key_missing_from_requested_language_falls_back_to_default_language(self):
        _write_catalog(self.directory, "en", {"welcome": "Welcome!"})
        _write_catalog(self.directory, "es", {})
        catalog = Catalog(self.directory, languages=("en", "es"), default_language="en")
        self.assertEqual(catalog.translate("welcome", "es"), "Welcome!")

    def test_key_missing_everywhere_falls_back_to_the_key_itself(self):
        _write_catalog(self.directory, "en", {})
        _write_catalog(self.directory, "es", {})
        catalog = Catalog(self.directory, languages=("en", "es"), default_language="en")
        self.assertEqual(catalog.translate("no_such_key", "es"), "no_such_key")

    def test_pluralizes_with_count_one(self):
        _write_catalog(self.directory, "en", {"item_count": {"one": "{count} item", "other": "{count} items"}})
        catalog = Catalog(self.directory, languages=("en",), default_language="en")
        self.assertEqual(catalog.translate("item_count", "en", count=1), "1 item")

    def test_pluralizes_with_count_other(self):
        _write_catalog(self.directory, "en", {"item_count": {"one": "{count} item", "other": "{count} items"}})
        catalog = Catalog(self.directory, languages=("en",), default_language="en")
        self.assertEqual(catalog.translate("item_count", "en", count=3), "3 items")

    def test_a_string_with_stray_braces_and_no_kwargs_is_returned_unchanged(self):
        _write_catalog(self.directory, "en", {"code_hint": "Use {{ }} syntax"})
        catalog = Catalog(self.directory, languages=("en",), default_language="en")
        self.assertEqual(catalog.translate("code_hint", "en"), "Use {{ }} syntax")

    def test_an_interpolation_failure_returns_the_raw_value_instead_of_raising(self):
        _write_catalog(self.directory, "en", {"greeting": "Hello, {name}!"})
        catalog = Catalog(self.directory, languages=("en",), default_language="en")
        self.assertEqual(catalog.translate("greeting", "en", wrong_kwarg="x"), "Hello, {name}!")

    def test_default_language_must_be_included_in_languages(self):
        with self.assertRaises(ValueError):
            Catalog(self.directory, languages=("en",), default_language="fr")

    def test_translate_defaults_to_the_catalogs_default_language(self):
        _write_catalog(self.directory, "en", {"welcome": "Welcome!"})
        catalog = Catalog(self.directory, languages=("en",), default_language="en")
        self.assertEqual(catalog.translate("welcome"), "Welcome!")


class LanguageCatalogTests(unittest.TestCase):
    def setUp(self):
        self._tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tempdir.cleanup)
        self.directory = Path(self._tempdir.name)
        _write_catalog(self.directory, "en", {"welcome": "Welcome!", "greeting": "Hello, {name}!"})
        self.catalog = Catalog(self.directory, languages=("en",), default_language="en")

    def test_call_translates_with_interpolation(self):
        t = self.catalog.for_language("en")
        self.assertEqual(t("greeting", name="Alice"), "Hello, Alice!")

    def test_attribute_access_translates_without_interpolation(self):
        t = self.catalog.for_language("en")
        self.assertEqual(t.welcome, "Welcome!")

    def test_attribute_access_on_a_missing_key_returns_the_key(self):
        t = self.catalog.for_language("en")
        self.assertEqual(t.no_such_key, "no_such_key")

    def test_underscore_prefixed_attribute_access_raises_attribute_error_not_a_translation(self):
        """`_catalog`/`_language` are real instance attributes, found by
        normal lookup before `__getattr__` ever runs -- this needs a
        private-looking name that does *not* already exist, so lookup
        actually falls through to `__getattr__`'s guard."""
        t = self.catalog.for_language("en")
        with self.assertRaises(AttributeError):
            t._nonexistent  # noqa: B018 -- intentionally triggers __getattr__'s guard

    def test_works_as_a_real_template_context_value(self):
        """The actual integration point this module exists for: templates.py's
        dotted-path resolver (`_resolve_attr`) calling getattr() on a
        LanguageCatalog, not just this module's own code agreeing with
        itself."""
        with tempfile.TemporaryDirectory() as template_directory:
            template_path = Path(template_directory) / "page.html"
            template_path.write_text("<h1>{{ t.welcome }}</h1><p>{{ t.no_such_key }}</p>", encoding="utf-8")
            t = self.catalog.for_language("en")
            rendered = render_template("page.html", {"t": t}, directories=[template_directory])
            self.assertEqual(rendered, "<h1>Welcome!</h1><p>no_such_key</p>")


class ResolveLanguageTests(unittest.TestCase):
    def test_exact_match_is_preferred(self):
        self.assertEqual(resolve_language("es,en;q=0.5", languages=("en", "es"), default="en"), "es")

    def test_highest_quality_wins_regardless_of_order(self):
        self.assertEqual(resolve_language("en;q=0.5,es;q=0.9", languages=("en", "es"), default="en"), "es")

    def test_ties_are_broken_by_original_order(self):
        self.assertEqual(resolve_language("fr,es", languages=("en", "es"), default="en"), "es")

    def test_primary_subtag_falls_back_to_a_supported_base_language(self):
        self.assertEqual(resolve_language("en-GB", languages=("en", "es"), default="es"), "en")

    def test_no_match_falls_back_to_default(self):
        self.assertEqual(resolve_language("fr,de", languages=("en", "es"), default="en"), "en")

    def test_empty_header_falls_back_to_default(self):
        self.assertEqual(resolve_language("", languages=("en", "es"), default="en"), "en")

    def test_wildcard_tag_is_ignored(self):
        self.assertEqual(resolve_language("*,es;q=0.1", languages=("en", "es"), default="en"), "es")

    def test_matching_is_case_insensitive(self):
        self.assertEqual(resolve_language("ES", languages=("en", "es"), default="en"), "es")

    def test_override_wins_outright_when_supported(self):
        self.assertEqual(resolve_language("en", languages=("en", "es"), default="en", override="es"), "es")

    def test_unsupported_override_falls_back_to_header_negotiation(self):
        self.assertEqual(resolve_language("es", languages=("en", "es"), default="en", override="fr"), "es")


class TranslatorForTests(unittest.TestCase):
    def setUp(self):
        self._tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tempdir.cleanup)
        self.directory = Path(self._tempdir.name)
        _write_catalog(self.directory, "en", {"welcome": "Welcome!"})
        _write_catalog(self.directory, "es", {"welcome": "¡Bienvenido!"})
        self.catalog = Catalog(self.directory, languages=("en", "es"), default_language="en")

    def _request(self, accept_language: str) -> Request:
        return Request(method="GET", path="/", query_params={}, body=b"", headers={"Accept-Language": accept_language})

    def test_resolves_language_from_the_request_header(self):
        t = translator_for(self._request("es"), self.catalog)
        self.assertIsInstance(t, LanguageCatalog)
        self.assertEqual(t.welcome, "¡Bienvenido!")

    def test_falls_back_to_default_language_when_header_is_absent(self):
        t = translator_for(self._request(""), self.catalog)
        self.assertEqual(t.welcome, "Welcome!")

    def test_override_takes_precedence_over_the_header(self):
        t = translator_for(self._request("en"), self.catalog, override="es")
        self.assertEqual(t.welcome, "¡Bienvenido!")


class ParseAcceptLanguageTests(unittest.TestCase):
    def test_an_entry_with_no_tag_text_is_skipped(self):
        """A `;`-led entry (e.g. a stray leading `;q=...`) partitions to an
        empty tag -- it must be dropped, not kept as a blank language."""
        self.assertEqual(_parse_accept_language("en,;q=0.9"), ["en"])

    def test_a_malformed_quality_value_defaults_to_zero(self):
        """`q=` followed by something `float()` rejects must not raise --
        it defaults to quality 0.0, so the entry still sorts, just last."""
        self.assertEqual(_parse_accept_language("en;q=not-a-number,es"), ["es", "en"])


class CatalogFromSettingsTests(unittest.TestCase):
    def test_builds_a_catalog_with_the_configured_languages_and_directory(self):
        catalog = catalog_from_settings({"LANGUAGES": ["en", "es"], "DEFAULT_LANGUAGE": "es", "LOCALE_DIR": "my_locale"})
        self.assertEqual(catalog.languages, ("en", "es"))
        self.assertEqual(catalog.default_language, "es")
        self.assertEqual(catalog.directory, Path("my_locale"))

    def test_defaults_to_english_only_when_unconfigured(self):
        catalog = catalog_from_settings({})
        self.assertEqual(catalog.languages, ("en",))
        self.assertEqual(catalog.default_language, "en")
        self.assertEqual(catalog.directory, Path("locale"))


if __name__ == "__main__":
    unittest.main()
