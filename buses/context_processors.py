def ad(request):
    path = request.path
    if (
        request.user.is_authenticated
        and not request.user.is_superuser
        or "/edit" in path
        or path.endswith(("/debug", "/block"))
        or "/blocks/" in path
        or path.startswith(
            ("/accounts/", "/fares/", "/sources/", "/journeys/", "/search", "/contact")
        )
        or "/tickets" in path
    ):
        return {"ad": False}

    return {"ad": True}
