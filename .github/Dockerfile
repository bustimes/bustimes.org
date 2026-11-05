FROM python:3.15-slim-trixie

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

# libgdal36: GDAL (and GEOS and PROJ) for GeoDjango - not gdal-bin, which pulls in a system Python
# (the version number in the name will need bumping when Debian updates GDAL)
# https://docs.djangoproject.com/en/6.1/ref/contrib/gis/install/geolibs/
RUN apt-get update && \
    apt-get install -y --no-install-recommends libgdal36 git gcc libc6-dev libpq-dev && \
    rm -rf /var/lib/apt && \
    rm -rf /var/lib/dpkg/info/*
