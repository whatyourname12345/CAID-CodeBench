from __future__ import annotations


DOMAIN_TAXONOMY = {
    "web_framework": {"django"},
    "ml_library": {"scikit-learn"},
    "scientific_computing": {"astropy", "xarray"},
    "static_analysis": {"pylint"},
    "symbolic_math": {"sympy"},
    "documentation_tooling": {"sphinx"},
    "visualization": {"matplotlib"},
    "testing_tooling": {"pytest"},
}


def repo_slug(repo: str) -> str:
    clean = str(repo or "").strip().lower()
    if "__" in clean:
        clean = clean.split("__", 1)[0] + "/" + clean.split("__", 1)[1]
    name = clean.rsplit("/", 1)[-1]
    if name in {"django"}:
        return "django"
    if "scikit-learn" in clean or name == "sklearn":
        return "scikit-learn"
    if "xarray" in clean:
        return "xarray"
    if "astropy" in clean:
        return "astropy"
    if "pylint" in clean:
        return "pylint"
    if "sympy" in clean:
        return "sympy"
    if "sphinx" in clean:
        return "sphinx"
    if "matplotlib" in clean:
        return "matplotlib"
    if "pytest" in clean:
        return "pytest"
    return name or "unknown"


def domain_for_repo(repo: str) -> str:
    slug = repo_slug(repo)
    for domain, slugs in DOMAIN_TAXONOMY.items():
        if slug in slugs:
            return domain
    return "other"
