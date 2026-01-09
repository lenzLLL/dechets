from rest_framework import serializers
from api.models import Subscription, User, Payment, Schedule, Collecte

class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ["id", "phone_number", "name", "role", "picture_url", "address", "city", "country", "created_at","updated_at"]

class UserMeSerializer(serializers.ModelSerializer):
    is_sadmin = serializers.SerializerMethodField()
    class Meta:
        model = User
        fields = [
            "id",
            "name",
            "phone_number",
            "picture_url",
            "role",
            "is_sadmin",
            "address",
            "city",
            "country",
            "created_at",
            "updated_at",
        ]

    def get_is_sadmin(self, obj):
        return obj.role == "SADMIN"


class SubscriptionSerializer(serializers.ModelSerializer):
    payments = serializers.SerializerMethodField()

    def get_payments(self, obj):
        payments_qs = getattr(obj, "payments", Payment.objects.none()).all()
        return PaymentSerializer(payments_qs, many=True).data

    class Meta:
        model = Subscription
        fields = [
            "id", "plan", "started_at", "expires_at","latitude","longitude","address","city","price",
            "is_active", "gateway", "gateway_subscription_id", "payments"
        ]


class PaymentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Payment
        fields = ["id", "amount", "currency", "status", "paid_at", "created_at", "gateway", "plan"]


class ScheduleSerializer(serializers.ModelSerializer):
    # return full videur info on read, accept videur id on write
    videur = UserMeSerializer(read_only=True)
    videur_id = serializers.PrimaryKeyRelatedField(queryset=User.objects.filter(role="BOUNCER"), write_only=True, required=False, allow_null=True, source='videur')
    subscription = serializers.PrimaryKeyRelatedField(queryset=Subscription.objects.all())

    class Meta:
        model = Schedule
        fields = ["id", "subscription", "videur", "videur_id", "slots"]

    def validate_slots(self, value):
        # slots must be a list of moments (day + time)
        if not isinstance(value, list):
            raise serializers.ValidationError("Slots must be a list")

        from datetime import datetime
        DAY_MAP = {
            1: 'Monday', 2: 'Tuesday', 3: 'Wednesday', 4: 'Thursday', 5: 'Friday', 6: 'Saturday', 7: 'Sunday'
        }

        normalized = []
        for slot in value:
            if not isinstance(slot, dict):
                raise serializers.ValidationError("Each slot must be an object")
            # require both 'day' and 'time'
            if 'day' not in slot or 'time' not in slot:
                raise serializers.ValidationError("Each slot must contain 'day' and 'time'")
            # validate time format HH:MM
            t = slot.get('time')
            try:
                datetime.strptime(t, '%H:%M')
            except Exception:
                raise serializers.ValidationError(f"Invalid time format in slot: {t}. Use HH:MM")

            day = slot.get('day')
            canonical = None
            # accept integer 1..7 or string digits
            if isinstance(day, int) or (isinstance(day, str) and day.isdigit()):
                try:
                    day_int = int(day)
                except Exception:
                    raise serializers.ValidationError(f"Invalid day value: {day}")
                if day_int < 1 or day_int > 7:
                    raise serializers.ValidationError("Day integer must be between 1 and 7 (1=Monday)")
                canonical = DAY_MAP[day_int]
            else:
                # allow English names (case-insensitive) and normalize
                if isinstance(day, str):
                    candidate = day.strip().lower()
                    for d in DAY_MAP.values():
                        if candidate == d.lower() or candidate == d[:3].lower():
                            canonical = d
                            break
                if not canonical:
                    raise serializers.ValidationError(f"Invalid day value: {day}. Use integer 1..7 or weekday name")

            new_slot = dict(slot)
            new_slot['day'] = canonical
            normalized.append(new_slot)

        # ensure no duplicate days within the same schedule
        days = [s['day'] for s in normalized]
        if len(days) != len(set(days)):
            raise serializers.ValidationError("Slots contain duplicate days; each day may appear only once per schedule")

        return normalized

    def validate(self, attrs):
        # ensure subscription provided (should be resolved to model instance)
        subscription = attrs.get('subscription') or getattr(self.instance, 'subscription', None)
        slots = attrs.get('slots') or getattr(self.instance, 'slots', [])

        # normalized count: each slot (day+time) counts as 1 occurrence
        total_occurrences = len(slots or [])

        if subscription:
            expected = getattr(subscription, 'collection_frequency', None)
            if expected is not None and total_occurrences != expected:
                raise serializers.ValidationError({
                    'slots': [f'Total number of scheduled days ({total_occurrences}) must equal subscription.collection_frequency ({expected})']
                })

        return attrs


class CollecteSerializer(serializers.ModelSerializer):
    client = UserMeSerializer(read_only=True)
    videur = UserMeSerializer(read_only=True)
    subscription = SubscriptionSerializer(read_only=True)
    subscription_id = serializers.PrimaryKeyRelatedField(
        queryset=Subscription.objects.all(),
        write_only=True,
        source='subscription'
    )
    date = serializers.DateTimeField(required=False)

    class Meta:
        model = Collecte
        fields = ["id", "client", "videur", "subscription", "subscription_id", "date", "status", "waste_type", "weight_kg", "created_at"]
        read_only_fields = ["created_at"]