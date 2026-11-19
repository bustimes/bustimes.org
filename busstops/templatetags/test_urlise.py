from django.test import SimpleTestCase

from .urlise import urlise


class UrliseTest(SimpleTestCase):
    def test_national_express(self):
        self.assertEqual(
            urlise("https://www.nationalexpress.com/en/destinations/manchester"),
            '<a href="https://nationalexpress.prf.hn/click/camref:1011ljPYw/destination:https://www.nationalexpress.com/en/destinations/manchester" rel="nofollow">nationalexpress.com/en/destinations/manchester</a>',
        )

        self.assertEqual(
            urlise("http://www.nationalexpress.com"),
            '<a href="https://nationalexpress.prf.hn/click/camref:1011ljPYw" rel="nofollow">nationalexpress.com</a>',
        )

    def test_flibco(self):
        self.assertEqual(
            urlise("https://www.flibco.com"),
            '<a href="https://www.awin1.com/cread.php?awinmid=53945&amp;awinaffid=242611&amp;ued=https%3A%2F%2Fwww.flibco.com" rel="nofollow">flibco.com</a>',
        )
        self.assertEqual(
            urlise("https://www.flibco.com/en/shuttle/bus-lille-airport"),
            '<a href="https://www.awin1.com/cread.php?awinmid=53945&amp;awinaffid=242611&amp;ued=https%3A%2F%2Fwww.flibco.com%2Fen%2Fshuttle%2Fbus-lille-airport" rel="nofollow">flibco.com/en/shuttle/bus-lille-airport</a>',
        )

    def test_multiple_urls(self):
        self.assertEqual(
            urlise("https://example.com/ https://example.com/"),
            """<a href="https://example.com/" rel="nofollow">example.com</a> <a href="https://example.com/" rel="nofollow">example.com</a>""",
        )
