from io import BytesIO
from unittest.mock import patch

from django.core.files.base import ContentFile
from django.core.files.storage import InMemoryStorage
from django.test import TestCase, override_settings
from PIL import ExifTags, Image

EXIF_TAG_IDS = {name: tag_id for tag_id, name in ExifTags.TAGS.items()}

from busstops.models import Operator, Region
from vehicles.models import Vehicle

from .detect import get_subject
from .exif import get_exif
from .models import Photo
from .processors import SmartCrop
from .tasks import detect_photo_subject, detect_photo_subject_blocking
from .utils import read_image


def make_jpeg(width, height, bus=None, exif=None):
    """A red image, optionally with a blue "bus" in it (a box given as
    fractions of the image size) and/or some EXIF tags
    """
    image = Image.new("RGB", (width, height), "red")
    if bus:
        x1, y1, x2, y2 = bus
        image.paste(
            Image.new(
                "RGB", (round((x2 - x1) * width), round((y2 - y1) * height)), "blue"
            ),
            (round(x1 * width), round(y1 * height)),
        )
    buf = BytesIO()
    if exif:
        image.save(buf, "JPEG", exif=exif)
    else:
        image.save(buf, "JPEG")
    return buf.getvalue()


def make_exif(gps=None, date_taken=None, **tags):
    """Build a PIL Exif object with the given (IFD0) tags, keyed by name
    (e.g. Make="..."), and optionally a GPSInfo sub-IFD
    ((lat, lat_ref, lon, lon_ref)) and DateTimeOriginal
    """
    exif = Image.Exif()
    for name, value in tags.items():
        exif[EXIF_TAG_IDS[name]] = value
    if gps:
        lat, lat_ref, lon, lon_ref = gps
        exif[34853] = {1: lat_ref, 2: lat, 3: lon_ref, 4: lon}
    if date_taken:
        exif.get_ifd(0x8769)[36867] = date_taken
    return exif


def blueness(image):
    """What proportion of the pixels are more blue than red"""
    pixels = list(image.convert("RGB").getdata())
    return sum(b > r for r, g, b in pixels) / len(pixels)


class SmartCropTest(TestCase):
    def test_grows_box_to_aspect_ratio(self):
        image = Image.new("RGB", (1000, 1000))

        # a wide box in the middle, cropped to a square
        cropped = SmartCrop([0.3, 0.4, 0.7, 0.6], padding=0).process(image)
        self.assertEqual(cropped.size, (400, 400))

        # a tall box, cropped to 2:1
        cropped = SmartCrop([0.4, 0.2, 0.6, 0.8], aspect=2, padding=0).process(image)
        self.assertEqual(cropped.size, (1000, 500))

    def test_padding(self):
        image = Image.new("RGB", (1000, 500))
        cropped = SmartCrop([0.4, 0.4, 0.6, 0.6], aspect=1, padding=0.5).process(image)
        # 200 wide + 50% padding either side = 400, and square
        self.assertEqual(cropped.size, (400, 400))

    def test_stays_inside_the_image(self):
        image = Image.new("RGB", (1000, 500))

        # a box in the corner - the crop should be nudged, not shrunk
        cropped = SmartCrop([0, 0, 0.2, 0.2], aspect=1, padding=0.5).process(image)
        self.assertEqual(cropped.size, (400, 400))

        # a box bigger than the image can be cropped to
        cropped = SmartCrop([0, 0, 1, 1], aspect=1, padding=0.5).process(image)
        self.assertEqual(cropped.size, (500, 500))


class GetSubjectTest(TestCase):
    def test_prefers_the_biggest_bus(self):
        subject = get_subject(
            [
                (5, 0.9, [0.1, 0.1, 0.3, 0.3]),  # a small bus
                (7, 0.6, [0.4, 0.1, 0.9, 0.8]),  # a bigger truck
                (2, 0.99, [0, 0, 1, 1]),  # a huge, confident car
            ]
        )
        self.assertEqual(subject, [0.4, 0.1, 0.9, 0.8])

    def test_falls_back_to_a_big_car(self):
        subject = get_subject(
            [
                (0, 0.9, [0, 0, 1, 1]),  # a person - not a vehicle
                (2, 0.7, [0.2, 0.2, 0.8, 0.8]),
            ]
        )
        self.assertEqual(subject, [0.2, 0.2, 0.8, 0.8])

    def test_ignores_small_cars(self):
        # a parked car in the background isn't what the photo is of
        self.assertIsNone(get_subject([(2, 0.9, [0.1, 0.1, 0.2, 0.2])]))

    def test_no_detections(self):
        self.assertIsNone(get_subject([]))

    def test_clamps_to_the_image(self):
        subject = get_subject([(5, 0.9, [-0.02, 0.1, 1.03, 0.9])])
        self.assertEqual(subject, [0, 0.1, 1, 0.9])


class ExifTest(TestCase):
    def test_no_exif(self):
        image = Image.open(BytesIO(make_jpeg(10, 10)))
        metadata, location, taken_at = get_exif(image)
        self.assertEqual(metadata, {})
        self.assertIsNone(location)
        self.assertIsNone(taken_at)

    def test_gps_and_date_taken(self):
        exif = make_exif(
            gps=((51.0, 30.0, 0.0), "N", (0.0, 7.0, 0.0), "W"),
            date_taken="2019:06:15 14:30:00",
            Make="TestCam",
        )
        image = Image.open(BytesIO(make_jpeg(10, 10, exif=exif)))
        metadata, location, taken_at = get_exif(image)

        self.assertEqual(metadata["Make"], "TestCam")
        self.assertAlmostEqual(location.y, 51.5)
        self.assertAlmostEqual(location.x, -0.1166666, places=5)
        self.assertEqual(str(taken_at), "2019-06-15 14:30:00+00:00")

    def test_strips_null_bytes_from_strings(self):
        # Postgres can't store 'em but some cameras pad strings with 'em
        exif = make_exif(Software="Ver.02.51\x00\x00\x00\x00")
        image = Image.open(BytesIO(make_jpeg(10, 10, exif=exif)))
        metadata, _, _ = get_exif(image)

        self.assertEqual(metadata, {"Software": "Ver.02.51"})

    def test_southern_and_eastern_hemispheres(self):
        exif = make_exif(gps=((33.0, 51.0, 0.0), "S", (151.0, 12.0, 0.0), "E"))
        image = Image.open(BytesIO(make_jpeg(10, 10, exif=exif)))
        _, location, _ = get_exif(image)

        self.assertLess(location.y, 0)  # Sydney is south of the equator
        self.assertGreater(location.x, 0)  # and east of Greenwich


@override_settings(
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
        "staticfiles": {
            "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
        },
    }
)
class PhotoTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        region = Region.objects.create(id="EA", name="East Anglia")
        operator = Operator.objects.create(
            region=region, name="Lynx", noc="LYNX", slug="lynx"
        )
        cls.vehicle = Vehicle.objects.create(
            code="2", fleet_number=2, operator=operator
        )

        photo = Photo(credit="Josh", caption="a bus")
        photo.image.save("bus.jpg", ContentFile(make_jpeg(1600, 900)))
        photo.vehicles.add(cls.vehicle)

    def test_vehicle_detail_photo(self):
        """shouldn't open the image from storage"""
        opens = []
        real_open = InMemoryStorage.open

        def counting_open(self, name, mode="rb"):
            opens.append(name)
            return real_open(self, name, mode)

        InMemoryStorage.open = counting_open
        try:
            response = self.client.get(self.vehicle.get_absolute_url())
        finally:
            InMemoryStorage.open = real_open

        self.assertContains(response, '<img src="')
        self.assertEqual(len(opens), 0)

    def test_vehicle_detail_photo_width_height(self):
        """width/height attributes appear on the <img> when known,
        still without opening the image from storage
        """
        photo = Photo.objects.get()
        photo.width, photo.height = 1600, 900
        photo.save(update_fields=["width", "height"])

        opens = []
        real_open = InMemoryStorage.open

        def counting_open(self, name, mode="rb"):
            opens.append(name)
            return real_open(self, name, mode)

        InMemoryStorage.open = counting_open
        try:
            response = self.client.get(self.vehicle.get_absolute_url())
        finally:
            InMemoryStorage.open = real_open

        self.assertContains(response, 'width="1600" height="900"')
        self.assertEqual(len(opens), 0)

    def test_read_image(self):
        """width, height, EXIF metadata and location get set from an
        uploaded image's own bytes
        """
        exif = make_exif(
            gps=((51.0, 30.0, 0.0), "N", (0.0, 7.0, 0.0), "W"),
            date_taken="2019:06:15 14:30:00",
            Make="Praktica",
        )
        photo = Photo()
        read_image(photo, make_jpeg(400, 300, exif=exif))

        self.assertEqual((photo.width, photo.height), (400, 300))
        self.assertEqual(photo.metadata["exif"]["Make"], "Praktica")
        self.assertAlmostEqual(photo.location.y, 51.5)
        self.assertEqual(str(photo.taken_at), "2019-06-15 14:30:00+00:00")

    def test_no_bounding_box(self):
        """with no detected vehicle, the whole photo is used, as before"""
        photo = Photo.objects.get()
        self.assertIsNone(photo.bbox)
        self.assertLess(blueness(Image.open(photo.image_320)), 0.01)
        self.assertEqual(Image.open(photo.image_1200_630).size, (1200, 630))

    def test_smart_crop(self):
        """the crops should close in on the detected vehicle"""
        bus = [0.5, 0.55, 0.95, 0.95]
        photo = Photo(caption="a bus", bbox=bus)
        photo.image.save("bus.jpg", ContentFile(make_jpeg(1600, 900, bus=bus)))

        thumbnail = Image.open(photo.image_320)
        # same shape as before, but now mostly bus
        self.assertEqual(thumbnail.size, (320, 180))
        self.assertGreater(blueness(thumbnail), 0.5)

        open_graph = Image.open(photo.image_1200_630)
        self.assertEqual(open_graph.size, (1200, 630))
        self.assertGreater(blueness(open_graph), 0.5)

    def test_moving_the_box_regenerates_the_crops(self):
        """so correcting a bad bounding box in the admin takes effect"""
        photo = Photo.objects.get()
        before = photo.image_320.name

        photo.bbox = [0.5, 0.55, 0.95, 0.95]
        self.assertNotEqual(photo.image_320.name, before)

    def test_detection_result_is_stored(self):
        photo = Photo.objects.get()
        with patch("photos.tasks.update_photo", return_value=False) as update_photo:
            result = detect_photo_subject(photo.id)

        update_photo.assert_called_once()
        self.assertIs(result(blocking=True, timeout=1), False)

    def test_detection_error_doesnt_break_the_upload(self):
        photo = Photo.objects.get()
        with (
            patch("photos.tasks.update_photo", side_effect=OSError),
            self.assertLogs("photos.tasks", "ERROR"),
        ):
            detect_photo_subject_blocking(photo.id)
