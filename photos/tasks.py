import logging

from huey.contrib.djhuey import db_task
from huey.exceptions import ResultTimeout

from .detect import update_photo
from .models import Photo

logger = logging.getLogger(__name__)

TIMEOUT = 10


@db_task()
def detect_photo_subject(photo_id: int) -> bool:
    """so each web server doesn't have to download and store the subject detection model"""
    try:
        return update_photo(Photo.objects.get(id=photo_id))
    except Exception:
        logger.exception("detecting subject of photo %d", photo_id)
        return False


def detect_photo_subject_blocking(photo_id: int) -> None:
    """wait (up to TIMEOUT seconds) for the cropped photo
    before displaying it
    """
    result = detect_photo_subject(photo_id)
    try:
        result(blocking=True, timeout=TIMEOUT)
    except ResultTimeout:
        logger.warning("timed out detecting subject of photo %d", photo_id)
