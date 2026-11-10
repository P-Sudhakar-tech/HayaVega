"""Translations for Hayavega -- plain JSON catalogs, not gettext.

Django does this with GNU gettext (`.po`/`.mo` files, `makemessages`/
`compilemessages` tooling) -- that tooling needs the external `msgfmt`
compiler, a system tool, not a Python package, which doesn't fit this
project's no-new-runtime-dependency, hand-rolled-over-imported pattern
(`graphql.py`, `templates.py`, `forms.py` all avoid third-party
dependencies the same way). Instead, a project hand-edits
`locale/en.json`/`locale/es.json` directly -- no extraction step, no
compiler, no new `hayavega` command needed at all.

```python
from hayavega.i18n import catalog_from_settings, translator_for

catalog = catalog_from_settings(settings.i18n)


def home(request):
    t = translator_for(request, catalog)
    return Response(render_template("home.html", {"t": t}))
```

```html title="templates/home.html"
<h1>{{ t.welcome }}</h1>
```

`templates.py`'s expression grammar supports only dotted-path lookups
(no function calls, no filters) -- `LanguageCatalog.__getattr__` is
what makes `{{ t.welcome }}` work, reusing that existing mechanism
rather than adding a new template tag.

**Not translated automatically**: built-in error messages
(`forms.py`'s field errors, `auth.py`'s `validate_password`, ...) --
threading a translator through those would touch many existing
function signatures across the codebase, out of scope here.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class Catalog:
    """Loads `<directory>/<language>.json` per language, lazily and cached
    in memory for this instance's lifetime -- catalogs are small, so unlike
    `templates.py`'s file cache this doesn't need mtime invalidation.

    A missing language file, or a key missing from it, falls back to
    `default_language`'s catalog, then (if still missing) to the key
    itself, unchanged -- the same "degrade gracefully, never crash a
    render" choice `render_template` makes for an undefined variable,
    and the same convention gettext itself uses for an untranslated
    `msgid`.
    """

    def __init__(self, directory: str | Path, *, languages: tuple[str, ...], default_language: str = "en"):
        if default_language not in languages:
            raise ValueError(f"default_language {default_language!r} must be included in languages {languages!r}.")
        self.directory = Path(directory)
        self.languages = tuple(languages)
        self.default_language = default_language
        self._loaded: dict[str, dict[str, Any]] = {}

    def _catalog_for(self, language: str) -> dict[str, Any]:
        if language not in self._loaded:
            path = self.directory / f"{language}.json"
            self._loaded[language] = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        return self._loaded[language]

    def translate(self, key: str, language: str | None = None, *, count: int | None = None, **kwargs: Any) -> str:
        """Look up `key` (optionally pluralized by `count`), then
        `str.format(**kwargs)` the result.

        A catalog value is either a plain string (interpolated directly)
        or a two-key `{"one": "...", "other": "..."}` dict selected by
        `count` -- a bounded, disclosed simplification: binary
        singular/plural only, not full CLDR plural-category rules
        (languages needing 3+ plural forms -- Russian, Arabic, Polish --
        aren't fully supported by this).
        """
        language = language or self.default_language
        entry: Any = self._catalog_for(language).get(key)
        if entry is None and language != self.default_language:
            entry = self._catalog_for(self.default_language).get(key)
        if entry is None:
            return key
        if isinstance(entry, dict):
            entry = entry.get("one" if count == 1 else "other", entry.get("other", key))
        text = str(entry)
        if count is not None:
            kwargs.setdefault("count", count)
        if not kwargs:
            return text
        try:
            return text.format(**kwargs)
        except (KeyError, IndexError):
            return text

    def for_language(self, language: str) -> LanguageCatalog:
        return LanguageCatalog(self, language)


class LanguageCatalog:
    """A `Catalog` bound to one language -- callable for explicit use in
    view code (`t("item_count", count=3)`, interpolation works), or
    dotted-path-accessible for a template (`{{ t.welcome }}`, no
    interpolation there, matching `templates.py`'s own dotted-path-only
    grammar).
    """

    def __init__(self, catalog: Catalog, language: str):
        self._catalog = catalog
        self._language = language

    def __call__(self, key: str, **kwargs: Any) -> str:
        return self._catalog.translate(key, self._language, **kwargs)

    def __getattr__(self, key: str) -> str:
        if key.startswith("_"):
            raise AttributeError(key)
        return self._catalog.translate(key, self._language)


def _parse_accept_language(header: str) -> list[str]:
    """Return the header's tags ordered by descending `q=` quality (ties
    broken by original order, matching RFC 4647's negotiation intent)."""
    entries = []
    for index, part in enumerate(header.split(",")):
        part = part.strip()
        if not part:
            continue
        tag, _, params = part.partition(";")
        tag = tag.strip()
        if not tag:
            continue
        quality = 1.0
        for param in params.split(";"):
            param = param.strip()
            if param.startswith("q="):
                try:
                    quality = float(param[2:])
                except ValueError:
                    quality = 0.0
        entries.append((quality, index, tag))
    entries.sort(key=lambda entry: (-entry[0], entry[1]))
    return [tag for _, _, tag in entries]


def resolve_language(accept_language: str, *, languages: tuple[str, ...], default: str, override: str | None = None) -> str:
    """Pick the best language for `languages` from `accept_language` (a raw
    `Accept-Language` header value) -- a pure function, not `Request`-aware,
    so a caller supplies `override` from wherever fits their app (a
    `?lang=` query param, a cookie, a session) rather than this module
    inventing another persistence mechanism `messages.py`/`security.py`
    already cover.

    `override` wins outright when it names a supported language.
    Otherwise each `Accept-Language` tag (highest `q=` first) is matched
    exactly against `languages`, then by primary subtag (`en-GB` matches
    a supported `en`). Falls back to `default` when nothing matches.
    """
    exact = {language.lower(): language for language in languages}
    primary: dict[str, str] = {}
    for language in languages:
        primary.setdefault(language.split("-")[0].lower(), language)

    if override is not None and override.lower() in exact:
        return exact[override.lower()]

    for tag in _parse_accept_language(accept_language):
        if tag == "*":
            continue
        tag_lower = tag.lower()
        if tag_lower in exact:
            return exact[tag_lower]
        primary_tag = tag_lower.split("-")[0]
        if primary_tag in primary:
            return primary[primary_tag]
    return default


def translator_for(request: Any, catalog: Catalog, *, override: str | None = None) -> LanguageCatalog:
    """The one-line convenience a view actually calls: resolve `request`'s
    language from its `Accept-Language` header (see `resolve_language` for
    `override`'s source) and return a `LanguageCatalog` bound to it."""
    language = resolve_language(request.headers.get("Accept-Language", ""), languages=catalog.languages, default=catalog.default_language, override=override)
    return catalog.for_language(language)


def catalog_from_settings(i18n: dict) -> Catalog:
    """Build the `Catalog` `Settings.i18n` configures -- the same
    `backend_from_settings()`/`cache_from_settings()` shape
    `hayavega.mail`/`hayavega.cache` already use. `LOCALE_DIR` defaults to
    `"locale"`, used as-is (no `base_dir` resolution), the same way
    `mail.FileEmailBackend`'s `directory` is.
    """
    languages = tuple(i18n.get("LANGUAGES", ("en",)))
    default_language = i18n.get("DEFAULT_LANGUAGE", languages[0] if languages else "en")
    directory = i18n.get("LOCALE_DIR", "locale")
    return Catalog(directory, languages=languages, default_language=default_language)
