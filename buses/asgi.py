import os
from channels.routing import ProtocolTypeRouter, URLRouter
from django.core.asgi import get_asgi_application
from django.urls import path
from vehicles.consumers import MyConsumer

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "buses.settings")

# Initialize Django ASGI application early to ensure the AppRegistry
# is populated before importing code that may import ORM models.
django_asgi_app = get_asgi_application()

application = ProtocolTypeRouter(
    {
        "http": django_asgi_app,
        "websocket": URLRouter(
            [
                path("vehicles/<int:id>", MyConsumer.as_asgi()),
            ]
        ),
    }
)
