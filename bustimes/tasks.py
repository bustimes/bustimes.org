from huey.contrib.djhuey import db_task

from .models import ImportTask


@db_task()
def handle_gtfs_upload(task_id: int, note=None):
    from .gtfs_utils import handle_gtfs_upload

    handle_gtfs_upload(ImportTask.objects.get(id=task_id), note)
