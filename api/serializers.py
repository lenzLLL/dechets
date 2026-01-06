from rest_framework import serializers
from api.models import Subscription, User, Payment

class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ["id", "phone_number", "name", "role", "picture_url", "created_at","updated_at","longitude","latitude","address","city","zipcode"]

class UserMeSerializer(serializers.ModelSerializer):
    is_sadmin = serializers.SerializerMethodField()
    class Meta:
        model = User
        fields = [
            "id",
            "name",
            "longitude",
            "latitude",
            "address",
            "city",
            "phone_number",
            "picture_url",
            "role",
            "is_sadmin",
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