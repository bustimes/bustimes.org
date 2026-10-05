import re

from django import template
from django.template.defaultfilters import stringfilter
from django.utils.html import urlize
from django.utils.safestring import mark_safe

from busstops.views import flibco_affiliate_link

register = template.Library()


@register.filter(is_safe=True, needs_autoescape=True)
@stringfilter
def urlise(value, autoescape=None):
    """Like the built-in Django urlize filter,
    but strips the 'https://www.' from the link text,
    and replaces Megabus and National Express URLs with venal affiliate ones
    """

    markup = (
        urlize(value, nofollow=True)
        .replace('">https://', '">')
        .replace('">http://', '">')
        .replace('">www.', '">')
    )
    markup = markup.replace("/</a>", "</a>")
    for url, affiliate_url in (
        (
            "http://www.nationalexpress.com",
            "https://nationalexpress.prf.hn/click/camref:1011ljPYw",
        ),
        (
            "https://www.flibco.com",
            flibco_affiliate_link(),
        ),
    ):
        url = f'"{url}"'
        if url in markup:
            markup = markup.replace(url, f'"{affiliate_url}"', 1)
            break

    # find a string like ' href="https://www.nationalexpress.com/en/destinations/manchester"'
    # (i.e. a deep link to any page on the national express website)
    # and prefix the url...
    markup = re.sub(
        r' href="(https://www\.nationalexpress\.com/en/[^"]+)"',
        r' href="https://nationalexpress.prf.hn/click/camref:1011ljPYw/destination:\1"',
        markup,
    )

    return mark_safe(markup)
