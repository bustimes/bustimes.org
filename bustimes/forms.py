from django.forms import CharField, FileField, Form


class UploadGTFSForm(Form):
    source_name = CharField(max_length=255)
    file = FileField(
        label="GTFS zip file", widget=FileField.widget(attrs={"accept": ".zip"})
    )
