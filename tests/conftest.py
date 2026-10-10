"""Hypothesis profiles: the default keeps the suite quick; run the deep search with
    pytest --hypothesis-profile=deep
"""

from hypothesis import HealthCheck, settings

settings.register_profile("default", max_examples=150, deadline=None,
                          suppress_health_check=[HealthCheck.too_slow])
settings.register_profile("deep", max_examples=3000, deadline=None,
                          suppress_health_check=[HealthCheck.too_slow, HealthCheck.filter_too_much])
settings.load_profile("default")
