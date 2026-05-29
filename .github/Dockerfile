FROM python:3.14-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

# install GDAL (https://docs.djangoproject.com/en/5.1/ref/contrib/gis/install/geolibs/)
RUN apt-get update && \
    apt-get install -y --no-install-recommends gdal-bin && \
    rm -rf /var/lib/apt && \
    rm -rf /var/lib/dpkg/info/*
