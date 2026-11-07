import re
from functools import wraps
from http import HTTPStatus

from django.conf import settings
from django.contrib.messages.storage.cookie import CookieStorage
from django.http import HttpResponse
from django.utils.cache import (
    add_never_cache_headers,
    patch_cache_control,
    patch_vary_headers,
)
from whitenoise.middleware import WhiteNoiseMiddleware


class HealthCheckMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path == "/up":
            # bypass ALLOWED_HOSTS check
            return HttpResponse("up!")

        return self.get_response(request)


class WhiteNoiseWithFallbackMiddleware(WhiteNoiseMiddleware):
    def immutable_file_test(self, path, url):
        # ensure that cache-control headers are added
        # for files with hashes added by parcel e.g. "dist/js/BigMap.19ec75b5.js"
        if re.match(r"^.+\.[0-9a-f]{8,12}\..+$", url):
            return True
        return super().immutable_file_test(path, url)

    # https://github.com/evansd/whitenoise/issues/245
    def __call__(self, request):
        response = super().__call__(request)
        if response.status_code == HTTPStatus.NOT_FOUND and request.path.startswith(
            self.static_prefix
        ):
            add_never_cache_headers(response)
        return response


def pin_db_middleware(get_response):
    from multidb.pinning import pin_this_thread, unpin_this_thread

    def middleware(request):
        if (
            request.method == "POST"
            or request.path.startswith("/admin/")
            or request.path.startswith("/accounts/")
            or "/edit" in request.path
        ):
            pin_this_thread()
        else:
            unpin_this_thread()
        return get_response(request)

    return middleware


def cdn_cache_control(
    max_age: int,
    stale_while_revalidate: int = 0,
    stale_if_error: int = 0,
    vary: tuple[str, ...] = (),
):
    """View decorator: let Cloudflare cache the response, but only for anonymous
    users. The decision is made later by AnonymousCdnCacheMiddleware, once
    session/messages/CSRF middleware have had their say.

    `vary` is for request headers the response depends on, e.g. CF-IPCountry
    """
    directives = [f"public, max-age={max_age}"]
    if stale_while_revalidate:
        directives.append(f"stale-while-revalidate={stale_while_revalidate}")
    if stale_if_error:
        directives.append(f"stale-if-error={stale_if_error}")
    header = ", ".join(directives)

    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            response = view_func(request, *args, **kwargs)
            if vary:
                patch_vary_headers(response, vary)
            response.cdn_cache_control = header
            return response

        return wrapper

    return decorator


# cookies whose presence might change what the page looks like
PERSONAL_COOKIES = (settings.SESSION_COOKIE_NAME, CookieStorage.cookie_name)


def is_anonymous_cacheable(request, response) -> bool:
    if request.method not in ("GET", "HEAD") or response.status_code != HTTPStatus.OK:
        return False
    if any(name in request.COOKIES for name in PERSONAL_COOKIES):
        return False
    if response.cookies:  # about to set a cookie (session, messages, csrftoken...)
        return False
    if "private" in response.get("Cache-Control", ""):
        return False
    user = getattr(request, "user", None)
    return not (user and user.is_authenticated)


class AnonymousCdnCacheMiddleware:
    """Must come before (i.e. wrap) SessionMiddleware, MessageMiddleware and the
    CSRF middleware, so that it sees any cookies they set on the response.

    Only responses from views decorated with @cdn_cache_control are affected.
    Anything personal is marked private/no-store, so it can never end up in
    Cloudflare's cache - but stopping a logged-in user from being *served* a
    cached anonymous page needs a Cloudflare cache rule bypassing the cache when
    the sessionid cookie is present (or for Cloudflare to honour Vary: Cookie)
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)

        header = getattr(response, "cdn_cache_control", None)
        if header is None:
            return response

        if is_anonymous_cacheable(request, response):
            response["CDN-Cache-Control"] = header
        else:
            response["CDN-Cache-Control"] = "private, no-store"
            # (patch_cache_control removes "public" when adding "private")
            patch_cache_control(response, private=True, no_store=True)

        return response
