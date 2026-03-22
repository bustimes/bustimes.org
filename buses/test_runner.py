import patchy
from django.test.runner import DiscoverRunner


class RandomOrderQuerySetRunner(DiscoverRunner):
    """
    Test runner that randomly orders all QuerySets without an explicit
    ordering, to catch code that assumes a specific database ordering.

    https://adamj.eu/tech/2023/07/04/django-test-random-order-querysets/
    """

    def setup_test_environment(self, **kwargs):
        super().setup_test_environment(**kwargs)

        from django.db.models.sql.compiler import SQLCompiler

        patchy.patch(
            SQLCompiler._order_by_pairs,
            """\
@@ -9,6 +9,8 @@
         ordering = meta.ordering
         self._meta_ordering = ordering
+    elif not self.query.distinct and not self.query.combinator:
+        ordering = ["?"]
     else:
         ordering = []
     if self.query.standard_ordering:
""",
        )
