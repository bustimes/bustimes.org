from django.forms import CharField, FileField, Form


class UploadGTFSForm(Form):
    source_name = CharField(max_length=255)
    file = FileField(
        label="GTFS zip file", widget=FileField.widget(attrs={"accept": ".zip"})
    )
    note = CharField(
        max_length=255,
        required=False,
        help_text="A note that will be added to every trip",
    )
