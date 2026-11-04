"""
WSGI config for buses project.

It exposes the WSGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/4.2/howto/deployment/wsgi/
"""

import os
import sys

from django.core.wsgi import get_wsgi_application
from django.urls import get_resolver

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "buses.settings")

if switch_interval := os.environ.get("PYTHON_SWITCH_INTERVAL"):
    sys.setswitchinterval(float(switch_interval))

application = get_wsgi_application()

# import every view module now, rather than during each worker's first request
get_resolver().url_patterns  # noqa: B018
