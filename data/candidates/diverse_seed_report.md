# Diverse Seed Candidate Report

- Input rows: 16778
- Selected rows: 20
- Reject/risky rows excluded upstream: 0
- Target size: 20
- Max per repo: 2
- Max domain ratio: 0.3
- Django/Web selected ratio: 2/20 (10.0%)

## Repo Distribution

- astropy/astropy: 2
- django/django: 2
- matplotlib/matplotlib: 2
- psf/requests: 2
- pydata/xarray: 2
- pytest-dev/pytest: 2
- scikit-learn/scikit-learn: 2
- sphinx-doc/sphinx: 2
- sympy/sympy: 2
- pylint-dev/pylint: 1
- mwaskom/seaborn: 1

## Domain Distribution

- scientific_computing: 4
- other: 3
- web_framework: 2
- visualization: 2
- testing_tooling: 2
- ml_library: 2
- documentation_tooling: 2
- symbolic_math: 2
- static_analysis: 1

## Manual Label Distribution

- : 20

## Rule Label Distribution

- candidate: 20

## Top Candidates

1. `astropy__astropy-12907` | repo=astropy/astropy | domain=scientific_computing | score=100 | risk=0
2. `astropy__astropy-13033` | repo=astropy/astropy | domain=scientific_computing | score=100 | risk=0
3. `django__django-10097` | repo=django/django | domain=web_framework | score=100 | risk=0
4. `django__django-10999` | repo=django/django | domain=web_framework | score=100 | risk=0
5. `matplotlib__matplotlib-20488` | repo=matplotlib/matplotlib | domain=visualization | score=100 | risk=0
6. `matplotlib__matplotlib-20826` | repo=matplotlib/matplotlib | domain=visualization | score=100 | risk=0
7. `psf__requests-1142` | repo=psf/requests | domain=other | score=100 | risk=0
8. `psf__requests-1724` | repo=psf/requests | domain=other | score=100 | risk=0
9. `pydata__xarray-2905` | repo=pydata/xarray | domain=scientific_computing | score=100 | risk=0
10. `pydata__xarray-3151` | repo=pydata/xarray | domain=scientific_computing | score=100 | risk=0
11. `pylint-dev__pylint-4970` | repo=pylint-dev/pylint | domain=static_analysis | score=100 | risk=0
12. `pytest-dev__pytest-10051` | repo=pytest-dev/pytest | domain=testing_tooling | score=100 | risk=0
13. `pytest-dev__pytest-10081` | repo=pytest-dev/pytest | domain=testing_tooling | score=100 | risk=0
14. `scikit-learn__scikit-learn-10297` | repo=scikit-learn/scikit-learn | domain=ml_library | score=100 | risk=0
15. `scikit-learn__scikit-learn-10844` | repo=scikit-learn/scikit-learn | domain=ml_library | score=100 | risk=0
16. `sphinx-doc__sphinx-10449` | repo=sphinx-doc/sphinx | domain=documentation_tooling | score=100 | risk=0
17. `sphinx-doc__sphinx-10614` | repo=sphinx-doc/sphinx | domain=documentation_tooling | score=100 | risk=0
18. `sympy__sympy-12096` | repo=sympy/sympy | domain=symbolic_math | score=100 | risk=0
19. `sympy__sympy-12419` | repo=sympy/sympy | domain=symbolic_math | score=100 | risk=0
20. `mwaskom__seaborn-3187` | repo=mwaskom/seaborn | domain=other | score=100 | risk=0

## Notes

- `django__django-14011` can remain a construction golden example, but the selected seed list is repo/domain-stratified.
- Use this CSV as input for CAIR v2 smoke batches before any larger conversion.
- Do not commit the full `data/candidates/` outputs by default; commit only the small sample if needed.
