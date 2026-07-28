from django.forms import TextInput


class UpperCaseTextInput(TextInput):
    def value_from_datadict(self, data, files, name):
        value = super().value_from_datadict(data, files, name)
        return value.upper() if value else value
