import hashlib
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path

import requests
from django.conf import settings
from django.contrib.gis.geos import Point
from django.core.files.base import ContentFile
from PIL import Image

from .exif import get_exif
from .models import Photo
from .tasks import detect_photo_subject_blocking


class WrongLicense(Exception):
    pass


def get_sha1(content):
    sha1 = hashlib.sha1(usedforsecurity=False)
    sha1.update(content)
    return sha1.hexdigest()


def read_image(photo, content):
    """Set a photo's width, height and EXIF metadata from the bytes of the
    image itself (already in memory - no extra request).

    Doesn't overwrite location/taken_at if already known from elsewhere
    (e.g. Flickr's own, possibly more reliable, values).
    """
    image = Image.open(BytesIO(content))
    photo.width, photo.height = image.size
    metadata, location, taken_at = get_exif(image)
    if metadata:
        photo.metadata["exif"] = metadata
    if location and not photo.location:
        photo.location = location
    if taken_at and not photo.taken_at:
        photo.taken_at = taken_at


def add_uploaded_photo(image, vehicle, request):
    photo = Photo()
    content = image.read()
    suffix = Path(image.name).suffix.lower() or ".jpg"
    read_image(photo, content)
    photo.image.save(get_sha1(content) + suffix, ContentFile(content))
    photo.user = request.user
    photo.livery_id = vehicle.livery_id
    photo.save()
    photo.vehicles.add(vehicle)
    detect_photo_subject_blocking(photo.id)


def add_flickr_photo(url, vehicle, request):
    photo_id = url.split("/photos/", 1)[1].split("/")[1]
    photo = Photo()
    session = requests.Session()
    session.headers.update({"User-Agent": "bustimes.org"})
    session.params = {
        "format": "json",
        "api_key": settings.FLICKR_API_KEY,
        "photo_id": photo_id,
        "nojsoncallback": 1,
    }
    response = session.get(
        "https://api.flickr.com/services/rest",
        params={"method": "flickr.photos.getInfo"},
        timeout=10,
    )
    response.raise_for_status()
    info = response.json()
    photo.url = info["photo"]["urls"]["url"][0]["_content"]

    photo.license = info["photo"]["license"]
    if photo.license in ("0", "1", "2", "3", "14", "15", "16"):
        raise WrongLicense()

    if info["photo"]["owner"]["path_alias"] != "goodwinjoshua":
        photo.credit = (
            info["photo"]["owner"]["realname"] or info["photo"]["owner"]["username"]
        )
    photo.caption = info["photo"]["title"]["_content"]
    photo.metadata["flickr"] = info["photo"]

    location = info["photo"].get("location")
    if location and location.get("latitude") not in (None, "0"):
        photo.location = Point(
            float(location["longitude"]), float(location["latitude"]), srid=4326
        )

    try:
        photo.taken_at = datetime.strptime(
            info["photo"]["dates"]["taken"], "%Y-%m-%d %H:%M:%S"
        ).replace(tzinfo=UTC)
    except ValueError:
        pass

    response = session.get(
        "https://api.flickr.com/services/rest",
        params={"method": "flickr.photos.getSizes"},
        timeout=10,
    )
    response.raise_for_status()
    sizes = response.json()
    url = sizes["sizes"]["size"][-1]["source"]
    original = session.get(url, timeout=10)
    read_image(photo, original.content)
    photo.image.save(get_sha1(original.content) + ".jpg", ContentFile(original.content))
    photo.user = request.user
    photo.livery_id = vehicle.livery_id
    photo.save()
    photo.vehicles.add(vehicle)
    detect_photo_subject_blocking(photo.id)
