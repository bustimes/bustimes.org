from datetime import UTC, date, datetime, timedelta

from django.db import transaction
from django.db.backends.postgresql.psycopg_any import DateTimeTZRange
from django.test import TransactionTestCase

from busstops.models import DataSource, Operator, PaymentMethod, Service, StopPoint
from disruptions.models import Consequence, Situation
from fares.models import DataSet, Tariff
from vehicles.models import Vehicle, VehicleFeature, VehicleJourney

from .models import Note, Route, StopTime, Trip


class DatabaseCascadeTest(TransactionTestCase):
    """The foreign keys from route down to stop time use on_delete=DB_CASCADE.

    Django's collector doesn't visit those rows at all, so nothing in Python
    checks that the database will actually let them go.  A missing ON DELETE on
    any of the tables involved - the implicit many-to-many tables especially -
    only shows up as an integrity error at COMMIT.
    """

    def test_deleting_a_route_cascades(self):
        source = DataSource.objects.create(name="test")
        route = Route.objects.create(source=source, code="test")
        trip = Trip.objects.create(
            route=route, start=timedelta(0), end=timedelta(minutes=1)
        )
        stop = StopPoint.objects.create(
            atco_code="test", common_name="Test Stop", active=True
        )
        stop_time = StopTime.objects.create(trip=trip, stop=stop, sequence=0)

        note = Note.objects.create(code="X", text="explanatory")
        trip.notes.add(note)
        stop_time.notes.add(note)

        vehicle = Vehicle.objects.create()
        journey = VehicleJourney.objects.create(
            trip=trip,
            vehicle=vehicle,
            source=source,
            datetime=datetime(2026, 8, 18, 9, tzinfo=UTC),
            date=date(2026, 8, 18),
        )

        # a transaction of its own, so a deferred constraint gets to fail
        with transaction.atomic():
            self.assertEqual(
                Route.objects.filter(id=route.id).delete(), (1, {"bustimes.Route": 1})
            )

        self.assertFalse(Trip.objects.filter(id=trip.id).exists())
        self.assertFalse(StopTime.objects.filter(id=stop_time.id).exists())
        self.assertFalse(Trip.notes.through.objects.exists())
        self.assertFalse(StopTime.notes.through.objects.exists())

        # the note itself is not a casualty
        self.assertTrue(Note.objects.filter(id=note.id).exists())

        # journey history survives, minus the link to the trip
        journey.refresh_from_db()
        self.assertIsNone(journey.trip_id)


class ManyToManyCascadeTest(TransactionTestCase):
    """The other end of the same relationships.

    Now that the through models say DB_CASCADE (or DO_NOTHING, where the model
    at the other end is deleted by Python), Django's collector no longer
    deletes these rows either way - the database has to.
    """

    def test_deleting_a_note_cascades(self):
        source = DataSource.objects.create(name="test")
        route = Route.objects.create(source=source, code="test")
        trip = Trip.objects.create(
            route=route, start=timedelta(0), end=timedelta(minutes=1)
        )
        note = Note.objects.create(code="X", text="explanatory")
        trip.notes.add(note)

        with transaction.atomic():
            Note.objects.filter(id=note.id).delete()

        self.assertFalse(Trip.notes.through.objects.exists())
        self.assertTrue(Trip.objects.filter(id=trip.id).exists())

    def test_deleting_an_operator_cascades(self):
        operator = Operator.objects.create(noc="TEST", name="Test")
        service = Service.objects.create(line_name="1")
        service.operator.add(operator)
        payment_method = PaymentMethod.objects.create(name="cash")
        operator.payment_methods.add(payment_method)

        with transaction.atomic():
            Operator.objects.filter(noc=operator.noc).delete()

        self.assertFalse(Service.operator.through.objects.exists())
        self.assertFalse(Operator.payment_methods.through.objects.exists())
        self.assertTrue(Service.objects.filter(id=service.id).exists())
        self.assertTrue(PaymentMethod.objects.filter(id=payment_method.id).exists())

    def test_deleting_a_service_cascades(self):
        """The DO_NOTHING side: only the database knows about this one."""

        operator = Operator.objects.create(noc="TEST", name="Test")
        service = Service.objects.create(line_name="1")
        service.operator.add(operator)

        with transaction.atomic():
            Service.objects.filter(id=service.id).delete()

        self.assertFalse(Service.operator.through.objects.exists())
        self.assertTrue(Operator.objects.filter(noc=operator.noc).exists())

    def test_deleting_a_vehicle_feature_cascades(self):
        vehicle = Vehicle.objects.create()
        feature = VehicleFeature.objects.create(name="USB chargers")
        vehicle.features.add(feature)

        with transaction.atomic():
            VehicleFeature.objects.filter(id=feature.id).delete()

        self.assertFalse(Vehicle.features.through.objects.exists())
        self.assertTrue(Vehicle.objects.filter(id=vehicle.id).exists())

    def test_deleting_the_python_side_cascades(self):
        """StopPoint and Tariff delete their rows by database cascade only."""

        operator = Operator.objects.create(noc="TEST", name="Test")
        stop = StopPoint.objects.create(
            atco_code="test", common_name="Test Stop", active=True
        )
        situation = Situation.objects.create(
            source=DataSource.objects.create(name="test"),
            publication_window=DateTimeTZRange("2026-09-01Z", "2026-09-30Z"),
        )
        consequence = Consequence.objects.create(situation=situation)
        consequence.stops.add(stop)

        tariff = Tariff.objects.create(
            source=DataSet.objects.create(name="test"), code="t", name="t", filename="t"
        )
        tariff.operators.add(operator)

        with transaction.atomic():
            StopPoint.objects.filter(atco_code=stop.pk).delete()
            Tariff.objects.filter(id=tariff.id).delete()

        self.assertFalse(Consequence.stops.through.objects.exists())
        self.assertFalse(Tariff.operators.through.objects.exists())
        self.assertTrue(Consequence.objects.filter(id=consequence.id).exists())
        self.assertTrue(Operator.objects.filter(noc=operator.noc).exists())

    def test_symmetrical_siblings(self):
        """A symmetrical self-referential m2m still writes and deletes both rows."""

        a = Operator.objects.create(noc="AAAA", name="A")
        b = Operator.objects.create(noc="BBBB", name="B")
        a.siblings.add(b)
        self.assertEqual(Operator.siblings.through.objects.count(), 2)
        self.assertEqual(list(b.siblings.all()), [a])

        # the admin edits siblings with set(), which removes as well as adds
        a.siblings.set([])
        self.assertFalse(Operator.siblings.through.objects.exists())
        a.siblings.add(b)

        with transaction.atomic():
            Operator.objects.filter(noc=b.noc).delete()

        self.assertFalse(Operator.siblings.through.objects.exists())
