import json
import re
import xml.etree.ElementTree as ET

from django.utils.html import mark_safe
from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import JsonLexer, XmlLexer


def minify(template_source):
    """Alternative to django_template_minifier's minify function"""
    if "<" in template_source and "<pre" not in template_source:
        template_source = re.sub(r"\n+ +", "\n", template_source)
    return template_source


def format_xml(text):
    formatter = HtmlFormatter()
    ET.register_namespace("", "http://www.siri.org.uk/siri")
    xml = ET.XML(text)
    ET.indent(xml)
    xml = ET.tostring(xml).decode()
    xml = mark_safe(highlight(xml, XmlLexer(), formatter))
    return formatter.get_style_defs(), xml


def format_json(text: dict):
    formatter = HtmlFormatter()
    text = json.dumps(text, indent=2)
    text = mark_safe(highlight(text, JsonLexer(), formatter))
    return formatter.get_style_defs(), text
