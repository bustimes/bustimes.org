import autoslug.fields
import busstops.fields
import django.db.models.deletion
import django.db.models.functions.datetime
import django.db.models.functions.text
import simple_history.models
import uuid
import vehicles.fields
import vehicles.models
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    replaces = [('vehicles', '0001_squashed_0003_sirisubscription'), ('vehicles', '0004_rename_datetime_vehiclerevision_created_at_and_more'), ('vehicles', '0005_vehiclerevision_disapproved_reason_and_more'), ('vehicles', '0006_remove_vehicleedit_arbiter_and_more'), ('vehicles', '0007_remove_historicallivery_locked_remove_livery_locked_and_more'), ('vehicles', '0008_vehiclerevision_unique_pending_operator_and_more'), ('vehicles', '0009_remove_vehiclerevision_unique_pending_operator_and_more'), ('vehicles', '0010_remove_historicallivery_css_remove_livery_css_and_more'), ('vehicles', '0011_historicallivery_show_name_livery_show_name_and_more'), ('vehicles', '0012_alter_historicallivery_updated_at_and_more'), ('vehicles', '0013_remove_vehiclerevision_score_delete_vehicleeditvote'), ('vehicles', '0014_alter_sirisubscription_name_and_more'), ('vehicles', '0015_vehiclejourney_date_alter_vehicle_slug_and_more'), ('vehicles', '0016_remove_vehiclejourney_service_datetime_date_and_more'), ('vehicles', '0017_alter_vehiclejourney_unique_together_and_more'), ('vehicles', '0018_vehiclejourney_route_name__date'), ('vehicles', '0019_sirisubscription_password_and_more'), ('vehicles', '0020_vehiclecode_unique_vehicle_code')]

    initial = True

    dependencies = [
        ('busstops', '0001_initial'),
        ('busstops', '0003_alter_stopcode_source'),
        ('busstops', '0011_alter_locality_slug_alter_operator_slug_and_more'),
        ('bustimes', '0001_initial'),
        ('bustimes', '0009_alter_trip_block_alter_trip_headsign_and_more'),
        ('bustimes', '0011_alter_timetabledatasource_url_alter_trip_block_and_more'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='Livery',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(db_index=True, max_length=255)),
                ('colour', models.CharField(help_text='For the most simplified version of the livery', max_length=7)),
                ('colours', models.CharField(blank=True, help_text="Keep it simple.\nSimplicity (and being able to read the route number on the map) is much more important than 'accuracy'.", max_length=512)),
                ('css', models.CharField(blank=True, help_text='Leave this blank.\nA livery can be adequately represented with a list of colours and an angle.', max_length=1024, verbose_name='CSS')),
                ('left_css', models.CharField(blank=True, help_text='Automatically generated from colours and angle', max_length=1024, verbose_name='Left CSS')),
                ('right_css', models.CharField(blank=True, help_text='Automatically generated from colours and angle', max_length=1024, verbose_name='Right CSS')),
                ('white_text', models.BooleanField(default=False)),
                ('text_colour', models.CharField(blank=True, max_length=7)),
                ('stroke_colour', models.CharField(blank=True, help_text='Use sparingly, often looks shit', max_length=7)),
                ('horizontal', models.BooleanField(default=False, help_text='Equivalent to setting the angle to 90')),
                ('angle', models.PositiveSmallIntegerField(blank=True, null=True)),
                ('locked', models.BooleanField(default=False)),
                ('updated_at', models.DateTimeField(blank=True, null=True)),
                ('published', models.BooleanField(help_text='Tick to include in the CSS and be able to apply this livery to vehicles')),
                ('operators', models.ManyToManyField(blank=True, related_name='liveries', to='busstops.operator')),
            ],
            options={
                'verbose_name_plural': 'liveries',
                'ordering': ('name',),
            },
        ),
        migrations.CreateModel(
            name='Vehicle',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('slug', autoslug.fields.AutoSlugField(editable=True, populate_from=vehicles.models.vehicle_slug, unique=True)),
                ('code', models.CharField(max_length=255)),
                ('fleet_number', models.PositiveIntegerField(blank=True, null=True)),
                ('fleet_code', models.CharField(blank=True, max_length=24)),
                ('reg', models.CharField(blank=True, max_length=24)),
                ('colours', models.CharField(blank=True, max_length=255)),
                ('name', models.CharField(blank=True, max_length=255)),
                ('branding', models.CharField(blank=True, max_length=255)),
                ('notes', models.CharField(blank=True, max_length=255)),
                ('latest_journey_data', models.JSONField(blank=True, null=True)),
                ('withdrawn', models.BooleanField(default=False)),
                ('data', models.JSONField(blank=True, null=True)),
                ('locked', models.BooleanField(default=False)),
            ],
        ),
        migrations.CreateModel(
            name='VehicleEdit',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('fleet_number', models.CharField(blank=True, max_length=24)),
                ('reg', models.CharField(blank=True, max_length=24)),
                ('vehicle_type', models.CharField(blank=True, max_length=255)),
                ('colours', models.CharField(blank=True, max_length=255)),
                ('name', models.CharField(blank=True, max_length=255)),
                ('branding', models.CharField(blank=True, max_length=255)),
                ('notes', models.CharField(blank=True, max_length=255)),
                ('withdrawn', models.BooleanField(null=True)),
                ('changes', models.JSONField(blank=True, null=True)),
                ('url', models.URLField(blank=True, max_length=255)),
                ('approved', models.BooleanField(db_index=True, null=True)),
                ('score', models.SmallIntegerField(default=0)),
                ('datetime', models.DateTimeField(blank=True, null=True)),
                ('arbiter', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='arbited', to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.CreateModel(
            name='VehicleFeature',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=255, unique=True)),
            ],
            options={
                'ordering': ('name',),
            },
        ),
        migrations.CreateModel(
            name='VehicleRevision',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('datetime', models.DateTimeField()),
                ('changes', models.JSONField(blank=True, null=True)),
                ('message', models.TextField(blank=True)),
            ],
        ),
        migrations.CreateModel(
            name='VehicleType',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=255, unique=True)),
                ('style', models.CharField(blank=True, choices=[('double decker', 'double decker'), ('minibus', 'minibus'), ('coach', 'coach'), ('articulated', 'articulated'), ('train', 'train'), ('tram', 'tram')], max_length=13)),
                ('fuel', models.CharField(blank=True, choices=[('diesel', 'diesel'), ('electric', 'electric'), ('hybrid', 'hybrid'), ('hydrogen', 'hydrogen'), ('gas', 'gas')], max_length=8)),
            ],
            options={
                'ordering': ('name',),
            },
        ),
        migrations.CreateModel(
            name='VehicleRevisionFeature',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('add', models.BooleanField(default=True)),
                ('feature', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='vehicles.vehiclefeature')),
                ('revision', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='vehicles.vehiclerevision')),
            ],
        ),
        migrations.AddField(
            model_name='vehiclerevision',
            name='features',
            field=models.ManyToManyField(blank=True, through='vehicles.VehicleRevisionFeature', to='vehicles.vehiclefeature'),
        ),
        migrations.AddField(
            model_name='vehiclerevision',
            name='from_livery',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='revision_from', to='vehicles.livery'),
        ),
        migrations.AddField(
            model_name='vehiclerevision',
            name='from_operator',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='revision_from', to='busstops.operator'),
        ),
        migrations.AddField(
            model_name='vehiclerevision',
            name='from_type',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='revision_from', to='vehicles.vehicletype'),
        ),
        migrations.AddField(
            model_name='vehiclerevision',
            name='to_livery',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='revision_to', to='vehicles.livery'),
        ),
        migrations.AddField(
            model_name='vehiclerevision',
            name='to_operator',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='revision_to', to='busstops.operator'),
        ),
        migrations.AddField(
            model_name='vehiclerevision',
            name='to_type',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='revision_to', to='vehicles.vehicletype'),
        ),
        migrations.AddField(
            model_name='vehiclerevision',
            name='user',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='vehiclerevision',
            name='vehicle',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='vehicles.vehicle'),
        ),
        migrations.CreateModel(
            name='VehicleJourney',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('datetime', models.DateTimeField()),
                ('route_name', models.CharField(blank=True, max_length=64)),
                ('code', models.CharField(blank=True, max_length=255)),
                ('destination', models.CharField(blank=True, max_length=255)),
                ('direction', models.CharField(blank=True, max_length=8)),
                ('uuid', models.UUIDField(default=uuid.uuid4, editable=False)),
                ('service', models.ForeignKey(blank=True, db_index=False, null=True, on_delete=django.db.models.deletion.SET_NULL, to='busstops.service')),
                ('source', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='busstops.datasource')),
                ('trip', models.ForeignKey(blank=True, db_index=False, null=True, on_delete=django.db.models.deletion.SET_NULL, to='bustimes.trip')),
                ('vehicle', models.ForeignKey(blank=True, db_index=False, null=True, on_delete=django.db.models.deletion.CASCADE, to='vehicles.vehicle')),
            ],
            options={
                'ordering': ('id',),
            },
        ),
        migrations.CreateModel(
            name='VehicleEditVote',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('positive', models.BooleanField()),
                ('by_user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to=settings.AUTH_USER_MODEL)),
                ('for_edit', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, to='vehicles.vehicleedit')),
                ('for_revision', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, to='vehicles.vehiclerevision')),
            ],
        ),
        migrations.CreateModel(
            name='VehicleEditFeature',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('add', models.BooleanField(default=True)),
                ('edit', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='vehicles.vehicleedit')),
                ('feature', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='vehicles.vehiclefeature')),
            ],
        ),
        migrations.AddField(
            model_name='vehicleedit',
            name='features',
            field=models.ManyToManyField(blank=True, through='vehicles.VehicleEditFeature', to='vehicles.vehiclefeature'),
        ),
        migrations.AddField(
            model_name='vehicleedit',
            name='livery',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to='vehicles.livery'),
        ),
        migrations.AddField(
            model_name='vehicleedit',
            name='user',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='vehicleedit',
            name='vehicle',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='vehicles.vehicle'),
        ),
        migrations.CreateModel(
            name='VehicleCode',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('code', models.CharField(max_length=100)),
                ('scheme', models.CharField(max_length=24)),
                ('vehicle', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='vehicles.vehicle')),
            ],
        ),
        migrations.AddField(
            model_name='vehicle',
            name='features',
            field=models.ManyToManyField(blank=True, to='vehicles.vehiclefeature'),
        ),
        migrations.AddField(
            model_name='vehicle',
            name='garage',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to='bustimes.garage'),
        ),
        migrations.AddField(
            model_name='vehicle',
            name='latest_journey',
            field=models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='latest_vehicle', to='vehicles.vehiclejourney'),
        ),
        migrations.AddField(
            model_name='vehicle',
            name='livery',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to='vehicles.livery'),
        ),
        migrations.AddField(
            model_name='vehicle',
            name='operator',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to='busstops.operator'),
        ),
        migrations.AddField(
            model_name='vehicle',
            name='source',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to='busstops.datasource'),
        ),
        migrations.AddField(
            model_name='vehicle',
            name='vehicle_type',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to='vehicles.vehicletype'),
        ),
        migrations.CreateModel(
            name='HistoricalLivery',
            fields=[
                ('id', models.IntegerField(auto_created=True, blank=True, db_index=True, verbose_name='ID')),
                ('name', models.CharField(db_index=True, max_length=255)),
                ('colour', models.CharField(help_text='For the most simplified version of the livery', max_length=7)),
                ('colours', models.CharField(blank=True, help_text="Keep it simple.\nSimplicity (and being able to read the route number on the map) is much more important than 'accuracy'.", max_length=512)),
                ('css', models.CharField(blank=True, help_text='Leave this blank.\nA livery can be adequately represented with a list of colours and an angle.', max_length=1024, verbose_name='CSS')),
                ('left_css', models.CharField(blank=True, help_text='Automatically generated from colours and angle', max_length=1024, verbose_name='Left CSS')),
                ('right_css', models.CharField(blank=True, help_text='Automatically generated from colours and angle', max_length=1024, verbose_name='Right CSS')),
                ('white_text', models.BooleanField(default=False)),
                ('text_colour', models.CharField(blank=True, max_length=7)),
                ('stroke_colour', models.CharField(blank=True, help_text='Use sparingly, often looks shit', max_length=7)),
                ('horizontal', models.BooleanField(default=False, help_text='Equivalent to setting the angle to 90')),
                ('angle', models.PositiveSmallIntegerField(blank=True, null=True)),
                ('locked', models.BooleanField(default=False)),
                ('updated_at', models.DateTimeField(blank=True, null=True)),
                ('published', models.BooleanField(help_text='Tick to include in the CSS and be able to apply this livery to vehicles')),
                ('history_id', models.AutoField(primary_key=True, serialize=False)),
                ('history_date', models.DateTimeField(db_index=True)),
                ('history_change_reason', models.CharField(max_length=100, null=True)),
                ('history_type', models.CharField(choices=[('+', 'Created'), ('~', 'Changed'), ('-', 'Deleted')], max_length=1)),
                ('history_user', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'historical livery',
                'verbose_name_plural': 'historical liveries',
                'ordering': ('-history_date', '-history_id'),
                'get_latest_by': ('history_date', 'history_id'),
            },
            bases=(simple_history.models.HistoricalChanges, models.Model),
        ),
        migrations.AddIndex(
            model_name='vehiclejourney',
            index=models.Index(models.F('service'), models.OrderBy(django.db.models.functions.datetime.TruncDate('datetime')), name='service_datetime_date'),
        ),
        migrations.AddIndex(
            model_name='vehiclejourney',
            index=models.Index(models.F('vehicle'), models.OrderBy(django.db.models.functions.datetime.TruncDate('datetime')), name='vehicle_datetime_date'),
        ),
        migrations.AlterUniqueTogether(
            name='vehiclejourney',
            unique_together={('vehicle', 'datetime')},
        ),
        migrations.AlterUniqueTogether(
            name='vehicleeditvote',
            unique_together={('by_user', 'for_edit')},
        ),
        migrations.AddIndex(
            model_name='vehiclecode',
            index=models.Index(fields=['code', 'scheme'], name='vehicles_ve_code_73ff06_idx'),
        ),
        migrations.AddIndex(
            model_name='vehicle',
            index=models.Index(django.db.models.functions.text.Upper('fleet_code'), name='fleet_code'),
        ),
        migrations.AddIndex(
            model_name='vehicle',
            index=models.Index(django.db.models.functions.text.Upper('reg'), name='reg'),
        ),
        migrations.AddIndex(
            model_name='vehicle',
            index=models.Index(fields=['operator', 'withdrawn'], name='operator_withdrawn'),
        ),
        migrations.AddConstraint(
            model_name='vehicle',
            constraint=models.UniqueConstraint(django.db.models.functions.text.Upper('code'), models.F('operator'), name='vehicle_operator_and_code'),
        ),
        migrations.CreateModel(
            name='SiriSubscription',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(blank=True, max_length=64, unique=True)),
                ('uuid', models.UUIDField(default=uuid.uuid4, editable=False)),
                ('sample', models.TextField(blank=True, null=True)),
            ],
        ),
        migrations.RenameField(
            model_name='vehiclerevision',
            old_name='datetime',
            new_name='created_at',
        ),
        migrations.AddField(
            model_name='vehiclerevision',
            name='approved_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='vehiclerevision',
            name='approved_by',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='approved', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='vehiclerevision',
            name='pending',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='vehiclerevision',
            name='disapproved',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='vehiclerevision',
            name='score',
            field=models.SmallIntegerField(default=0),
        ),
        migrations.AddField(
            model_name='vehiclerevision',
            name='disapproved_reason',
            field=models.TextField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name='vehiclerevision',
            name='message',
            field=models.TextField(blank=True, null=True),
        ),
        migrations.RemoveField(
            model_name='vehicleedit',
            name='arbiter',
        ),
        migrations.RemoveField(
            model_name='vehicleedit',
            name='features',
        ),
        migrations.RemoveField(
            model_name='vehicleedit',
            name='livery',
        ),
        migrations.RemoveField(
            model_name='vehicleedit',
            name='user',
        ),
        migrations.RemoveField(
            model_name='vehicleedit',
            name='vehicle',
        ),
        migrations.AlterUniqueTogether(
            name='vehicleeditvote',
            unique_together={('by_user', 'for_revision')},
        ),
        migrations.DeleteModel(
            name='VehicleEditFeature',
        ),
        migrations.RemoveField(
            model_name='vehicleeditvote',
            name='for_edit',
        ),
        migrations.DeleteModel(
            name='VehicleEdit',
        ),
        migrations.RemoveField(
            model_name='historicallivery',
            name='locked',
        ),
        migrations.RemoveField(
            model_name='livery',
            name='locked',
        ),
        migrations.RemoveField(
            model_name='livery',
            name='operators',
        ),
        migrations.AddConstraint(
            model_name='vehiclerevision',
            constraint=models.UniqueConstraint(condition=models.Q(('pending', True)), fields=('to_operator',), name='unique_pending_operator'),
        ),
        migrations.AddConstraint(
            model_name='vehiclerevision',
            constraint=models.UniqueConstraint(condition=models.Q(('pending', True)), fields=('to_type',), name='unique_pending_type'),
        ),
        migrations.AddConstraint(
            model_name='vehiclerevision',
            constraint=models.UniqueConstraint(condition=models.Q(('pending', True)), fields=('to_livery',), name='unique_pending_livery'),
        ),
        migrations.RemoveConstraint(
            model_name='vehiclerevision',
            name='unique_pending_operator',
        ),
        migrations.RemoveConstraint(
            model_name='vehiclerevision',
            name='unique_pending_type',
        ),
        migrations.RemoveConstraint(
            model_name='vehiclerevision',
            name='unique_pending_livery',
        ),
        migrations.AddConstraint(
            model_name='vehiclerevision',
            constraint=models.UniqueConstraint(condition=models.Q(('pending', True)), fields=('vehicle', 'to_operator'), name='unique_pending_operator'),
        ),
        migrations.AddConstraint(
            model_name='vehiclerevision',
            constraint=models.UniqueConstraint(condition=models.Q(('pending', True)), fields=('vehicle', 'to_type'), name='unique_pending_type'),
        ),
        migrations.AddConstraint(
            model_name='vehiclerevision',
            constraint=models.UniqueConstraint(condition=models.Q(('pending', True)), fields=('vehicle', 'to_livery'), name='unique_pending_livery'),
        ),
        migrations.RemoveField(
            model_name='historicallivery',
            name='css',
        ),
        migrations.RemoveField(
            model_name='livery',
            name='css',
        ),
        migrations.AlterField(
            model_name='vehicletype',
            name='style',
            field=models.CharField(blank=True, choices=[('', 'single decker'), ('double decker', 'double decker'), ('minibus', 'minibus'), ('coach', 'coach'), ('decker coach', 'double decker coach'), ('articulated', 'bendy bus'), ('train', 'train'), ('tram', 'tram'), ('amphibious', 'amphibious')], max_length=13),
        ),
        migrations.AddField(
            model_name='historicallivery',
            name='show_name',
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name='livery',
            name='show_name',
            field=models.BooleanField(default=True),
        ),
        migrations.AlterField(
            model_name='historicallivery',
            name='colour',
            field=vehicles.fields.ColourField(help_text='For the most simplified version of the livery', max_length=7),
        ),
        migrations.AlterField(
            model_name='historicallivery',
            name='colours',
            field=vehicles.fields.ColoursField(blank=True, help_text='Left and right CSS will be generated from this', max_length=512),
        ),
        migrations.AlterField(
            model_name='historicallivery',
            name='left_css',
            field=vehicles.fields.CSSField(blank=True, help_text='Automatically generated from colours and angle', max_length=1024, verbose_name='Left CSS'),
        ),
        migrations.AlterField(
            model_name='historicallivery',
            name='published',
            field=models.BooleanField(default=False, help_text='Tick to include in the CSS and be able to apply this livery to vehicles'),
        ),
        migrations.AlterField(
            model_name='historicallivery',
            name='right_css',
            field=vehicles.fields.CSSField(blank=True, help_text='Should be a mirror image of the left CSS', max_length=1024, verbose_name='Right CSS'),
        ),
        migrations.AlterField(
            model_name='historicallivery',
            name='stroke_colour',
            field=vehicles.fields.ColourField(blank=True, help_text='Use sparingly, often looks shit', max_length=7),
        ),
        migrations.AlterField(
            model_name='historicallivery',
            name='text_colour',
            field=vehicles.fields.ColourField(blank=True, max_length=7),
        ),
        migrations.AlterField(
            model_name='livery',
            name='colour',
            field=vehicles.fields.ColourField(help_text='For the most simplified version of the livery', max_length=7),
        ),
        migrations.AlterField(
            model_name='livery',
            name='colours',
            field=vehicles.fields.ColoursField(blank=True, help_text='Left and right CSS will be generated from this', max_length=512),
        ),
        migrations.AlterField(
            model_name='livery',
            name='left_css',
            field=vehicles.fields.CSSField(blank=True, help_text='Automatically generated from colours and angle', max_length=1024, verbose_name='Left CSS'),
        ),
        migrations.AlterField(
            model_name='livery',
            name='published',
            field=models.BooleanField(default=False, help_text='Tick to include in the CSS and be able to apply this livery to vehicles'),
        ),
        migrations.AlterField(
            model_name='livery',
            name='right_css',
            field=vehicles.fields.CSSField(blank=True, help_text='Should be a mirror image of the left CSS', max_length=1024, verbose_name='Right CSS'),
        ),
        migrations.AlterField(
            model_name='livery',
            name='stroke_colour',
            field=vehicles.fields.ColourField(blank=True, help_text='Use sparingly, often looks shit', max_length=7),
        ),
        migrations.AlterField(
            model_name='livery',
            name='text_colour',
            field=vehicles.fields.ColourField(blank=True, max_length=7),
        ),
        migrations.AlterField(
            model_name='vehicle',
            name='colours',
            field=vehicles.fields.ColoursField(blank=True, max_length=255),
        ),
        migrations.AlterField(
            model_name='historicallivery',
            name='updated_at',
            field=models.DateTimeField(blank=True, editable=False, null=True),
        ),
        migrations.AlterField(
            model_name='livery',
            name='updated_at',
            field=models.DateTimeField(auto_now=True, null=True),
        ),
        migrations.RemoveField(
            model_name='vehiclerevision',
            name='score',
        ),
        migrations.DeleteModel(
            name='VehicleEditVote',
        ),
        migrations.AlterField(
            model_name='sirisubscription',
            name='name',
            field=models.CharField(blank=True, help_text='There should be a DataSource with the same name as this', max_length=64, unique=True),
        ),
        migrations.AlterField(
            model_name='vehiclejourney',
            name='direction',
            field=models.CharField(blank=True, max_length=13),
        ),
        migrations.AddField(
            model_name='vehiclejourney',
            name='date',
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name='vehicle',
            name='slug',
            field=busstops.fields.AutoSlugField(editable=True, populate_from=vehicles.models.vehicle_slug, unique=True),
        ),
        migrations.AddIndex(
            model_name='vehiclejourney',
            index=models.Index(models.F('service'), models.F('date'), condition=models.Q(('date__isnull', False)), name='service_date'),
        ),
        migrations.AddIndex(
            model_name='vehiclejourney',
            index=models.Index(models.F('vehicle'), models.F('date'), condition=models.Q(('date__isnull', False), ('vehicle__isnull', False)), name='vehicle_date'),
        ),
        migrations.AddIndex(
            model_name='vehiclejourney',
            index=models.Index(models.F('service'), models.F('date'), name='vehiclejourney_service_date'),
        ),
        migrations.AddIndex(
            model_name='vehiclejourney',
            index=models.Index(models.F('vehicle'), models.F('date'), condition=models.Q(('vehicle__isnull', False)), name='vehiclejourney_vehicle_date'),
        ),
        migrations.AddIndex(
            model_name='vehiclejourney',
            index=models.Index(models.F('trip'), models.F('date'), condition=models.Q(('trip__isnull', False)), name='vehiclejourney_trip_date'),
        ),
        migrations.RemoveIndex(
            model_name='vehiclejourney',
            name='service_datetime_date',
        ),
        migrations.RemoveIndex(
            model_name='vehiclejourney',
            name='vehicle_datetime_date',
        ),
        migrations.RemoveIndex(
            model_name='vehiclejourney',
            name='service_date',
        ),
        migrations.RemoveIndex(
            model_name='vehiclejourney',
            name='vehicle_date',
        ),
        migrations.AlterUniqueTogether(
            name='vehiclejourney',
            unique_together=set(),
        ),
        migrations.AlterField(
            model_name='vehiclejourney',
            name='date',
            field=models.DateField(),
        ),
        migrations.AddIndex(
            model_name='vehiclejourney',
            index=models.Index(models.F('route_name'), models.F('date'), condition=models.Q(('service__isnull', True)), name='route_name__date'),
        ),
        migrations.AddField(
            model_name='sirisubscription',
            name='password',
            field=models.CharField(blank=True, max_length=64, null=True),
        ),
        migrations.AddField(
            model_name='sirisubscription',
            name='producer_url',
            field=models.URLField(blank=True, max_length=64, null=True),
        ),
        migrations.AddField(
            model_name='sirisubscription',
            name='requestor_ref',
            field=models.CharField(blank=True, max_length=64, null=True),
        ),
        migrations.AddField(
            model_name='sirisubscription',
            name='username',
            field=models.CharField(blank=True, max_length=64, null=True),
        ),
        migrations.AddConstraint(
            model_name='vehiclecode',
            constraint=models.UniqueConstraint(fields=('code', 'scheme'), name='unique_vehicle_code'),
        ),
    ]
