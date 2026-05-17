"""Tests for the buses app"""

from django.test import TestCase, RequestFactory

from . import utils, wsgi, asgi


class UtilsTests(TestCase):
    """Tests for the buses.utils module"""

    def test_minify(self):
        """Test that the minify function minifies (while preserving some characters) as expected"""
        self.assertEqual(
            utils.minify(
                """
                   \n
            <marquee>
                {% if foo %}\n     \n                    {% if bar %}
                        <strong>Golf sale</strong>  \n
                    {% endif %}
                {% endif %}
            </marquee>
            """
            ),
            """

<marquee>
{% if foo %}

{% if bar %}
<strong>Golf sale</strong>  \n{% endif %}
{% endif %}
</marquee>
""",
        )


class WSGITest(TestCase):
    def test_wsgi_and_asgi(self):
        rf = RequestFactory()

        resolver_match_1 = wsgi.application.resolve_request(rf.get("/"))
        self.assertEqual(resolver_match_1.url_name, "index")

        resolver_match_2 = asgi.django_asgi_app.resolve_request(rf.get("/"))

        self.assertEqual(str(resolver_match_1), str(resolver_match_2))
