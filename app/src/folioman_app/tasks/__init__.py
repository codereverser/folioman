"""Service-layer orchestration: import processors + reconcile.

Importing this package registers the per-kind import processors with the job
runner (folioman_app.services.imports). AppConfig.ready imports it at startup.
"""

from folioman_app.tasks import (
    import_cas,  # noqa: F401 — registers the unified "cas" processor (MF CAS + eCAS)
    import_csv,  # noqa: F401 — registers the csv processor (currently disabled)
)
