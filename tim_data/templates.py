"""BrowserAct template IDs and parameter names, taken from browser-act/skills
(solutions/lead-generation/* and solutions/ecommerce/*)."""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Template:
    template_id: str
    description: str
    # CLI option -> API parameter name, with default (None = required)
    params: dict[str, tuple[str, object]] = field(default_factory=dict)


TEMPLATES: dict[str, Template] = {
    "maps": Template(
        "77577579210625331",
        "Google Maps: business name, category, address, phone, website, rating",
        {"keywords": ("keywords", None), "language": ("language", "en"), "country": ("country", "us")},
    ),
    "maps-search": Template(
        "77805072070738748",
        "Google Maps search with limit: name, address, rating, price range, amenities",
        {
            "keywords": ("KeyWords", None),
            "language": ("language", "en"),
            "country": ("country", "us"),
            "limit": ("max_dates", 100),
        },
    ),
    "maps-reviews": Template(
        "77806855016940604",
        "Google Maps reviews for a place: author, rating, text, date",
        {"keywords": ("KeyWords", None), "language": ("language", "en"), "country": ("country", "us")},
    ),
    "amazon-search": Template(
        "77809217106347580",
        "Amazon search: title, URL, price, rating, reviews, monthly sales",
        {
            "keywords": ("KeyWords", None),
            "brand": ("Brand", ""),
            "limit": ("Maximum_date", 50),
            "language": ("language", "en"),
        },
    ),
    "amazon-reviews": Template(
        "77817507798321724",
        "Amazon reviews by ASIN",
        {"asin": ("ASIN", None)},
    ),
}
