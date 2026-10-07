from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand

from busstops.models import DataSource

from ...utils import get_sha1, log_time_taken


class Command(BaseCommand):
    """Downloads fresh Traveline National Dataset (TNDS) data
    from the password-protected FTP server
    (I know, not my choice of technology)
    and calls the import_transxchange command"""

    @staticmethod
    def add_arguments(parser):
        parser.add_argument("username", type=str)
        parser.add_argument("password", type=str)

    def list_files(self) -> dict:
        # excluding London - we use original TfL data instead
        files = [
            (name, details)
            for name, details in self.ftp.mlsd()
            if name.endswith(".zip") and name != "L.zip"
        ]
        files.sort(key=lambda item: int(item[1]["size"]))  # smallest files first
        return dict(files)

    def do_files(self, files: dict):
        for name, details in files.items():
            self.do_file(name, details)

    def do_file(self, name, details):
        source, _ = DataSource.objects.get_or_create(
            url=f"ftp://{self.ftp.host}/{name}"
        )

        path = settings.TNDS_DIR / name

        # yes we use file size as a hash function
        # to decide whether to download the file
        if not path.exists() or path.stat().st_size != int(details["size"]):
            with open(path, "wb") as open_file:
                self.ftp.retrbinary(f"RETR {name}", open_file.write)

        sha1 = get_sha1(path)
        if sha1 != source.sha1:
            source.sha1 = sha1
            self.changed_files.append((path, source))

    def handle(self, username, password, *args, **options):
        import logging
        from ftplib import FTP

        logger = logging.getLogger(__name__)

        self.ftp = FTP(
            host="ftp.tnds.basemap.co.uk", user=username, passwd=password, timeout=120
        )

        self.changed_files = []
        self.ftp.cwd("TNDSV2.5")
        v2_files = self.list_files()
        self.do_files(v2_files)

        self.ftp.quit()

        for file, source in self.changed_files:
            logger.info(file.name)
            with log_time_taken(logger):
                call_command("import_transxchange", file)

            source.save(update_fields=["sha1"])
