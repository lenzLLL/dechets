from django.shortcuts import get_object_or_404
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework import status
from django.utils import timezone
from datetime import timedelta
from rest_framework_simplejwt.tokens import RefreshToken
from decimal import Decimal
from api.models import Subscription, User, OTP, Notification, Payment
from api.serializers import  SubscriptionSerializer, UserSerializer
from api.services.whatsapp import send_otp_whatsapp
from api.permissions import IsAuthenticatedUser, IsSuperAdmin
from rest_framework.decorators import api_view, permission_classes, authentication_classes
from api.services.notify import create_and_send_whatsapp_notification


@api_view(["POST"])
@authentication_classes([])
def send_otp_view(request):
    phone = request.data.get("phone")

    if not phone:
        return Response({"error": "Le numéro est requis"}, status=400)

    result = send_otp_whatsapp(phone)

    if result.get("status") == "error":
        return Response(result, status=status.HTTP_429_TOO_MANY_REQUESTS)

    return Response({"message": "OTP envoyé"}, status=200)

@api_view(["POST"])
@authentication_classes([])
def verify_otp_view(request):
    phone = request.data.get("phone")
    code = request.data.get("code")

    if not phone or not code:
        return Response({"error": "phone et code obligatoires"}, status=400)

    # 1. Vérification OTP
    try:
        otp_obj = OTP.objects.get(phone=phone, otp=code)
    except OTP.DoesNotExist:
        return Response({"error": "OTP incorrect"}, status=400)

    if otp_obj.is_expired():
        return Response({"error": "OTP expiré"}, status=400)
    

    # 2. Récupérer / créer l'utilisateur
    user, created = User.objects.get_or_create(
        phone_number=phone,
    )
    if created:
        Notification.objects.create(
            user=user,
            title="Bienvenue Sur Photizon",
            eng_title="Welcome To Photizon",
            message="Bienvenue sur Photizon ! Veuillez entrer le code de votre église pour accéder aux contenus de votre communauté et rester connecté avec votre famille d’église..",
            eng_message="Welcome to Photizon! Please enter your church code to access your community’s content and stay connected with your church family.",
            type="SUCCESS"
        )
        create_and_send_whatsapp_notification(
        user=user,
        title_eng="Welcome to Photizon",
        title="Bienvenue sur Photizon",
        message="Bienvenue sur Photizon ! Veuillez entrer le code de votre église pour accéder aux contenus.",
        message_eng="Welcome to Photizon! Please enter your church code to access your community’s content and stay connected with your church family.",
        template_name="welcome_message",  # Nom du template WhatsApp que tu as créé sur Meta
        template_params=[user.phone_number]  # Paramètres dynamiques si nécessaire
        )
    # 3. Générer le token JWT (access + refresh)
    refresh = RefreshToken.for_user(user)

    # 4. Supprimer l'OTP après succès
    otp_obj.delete()

    # 5. Retour
    message = "Nouveau compte créé" if created else "Utilisateur existant connecté"

    return Response({
        "success": True,
        "message": message,
        "is_new_user": created,
        "user": UserSerializer(user).data,
        "access": str(refresh.access_token),
        "refresh": str(refresh)
    }, status=200)


@api_view(["GET"])
@permission_classes([IsAuthenticatedUser])
def get_church_subscription(request):
    user = request.user
    sub = get_object_or_404(Subscription, client=user)
    if not sub:
        return Response({"detail": "No subscription"}, status=404)
    return Response(SubscriptionSerializer(sub).data)

@api_view(["PUT", "PATCH"])
@permission_classes([IsAuthenticatedUser])
def update_subscription(request):
    user = request.user
    sub, created = Subscription.objects.get_or_create(
        client=user,
        defaults={
            "expires_at": timezone.now() + timedelta(days=30)
        }
    )
    data = request.data.copy()
    
    if "expires_at" not in data or not data.get("expires_at"):
        # seulement si c'est une mise à jour partielle
        if not sub.expires_at:
            data["expires_at"] = (timezone.now() + timedelta(days=30)).isoformat()

    serializer = SubscriptionSerializer(sub, data=data, partial=True)

    if serializer.is_valid():
        sub = serializer.save()

        # Create a Payment each time a subscription is created
        if created:
            try:
                Payment.objects.create(
                    client=user,
                    subscription=sub,
                    plan=sub.plan,
                    amount=Decimal(getattr(sub, "price", 0) or 0),
                    currency=getattr(sub, "currency", "XAF") or "XAF",
                    gateway=getattr(sub, "gateway", None),
                    gateway_subscription_id=getattr(sub, "gateway_subscription_id", None),
                    status="success",
                    paid_at=timezone.now()
                )
            except Exception:
                # Do not block subscription update on payment creation errors
                pass

        return Response({
            "created": created,      # True = subscription auto-créée
            "subscription": serializer.data
        })

    return Response(serializer.errors, status=400)

@api_view(["DELETE"])
@permission_classes([IsAuthenticatedUser])
def delete_subscription(request):
    user = request.user
    sub = getattr(user, "subscription", None)

    if not sub:
        return Response({"detail": "No subscription"}, status=404)

    sub.delete()
    return Response({"detail": "Subscription deleted"})

@api_view(["GET"])
@permission_classes([IsAuthenticatedUser])
def check_subscription_status(request):

    user = request.user
    sub = getattr(user, "subscription", None)

    if not sub:
        return Response({"status": "none"})

    now = timezone.now()
    status_value = "active" if sub.is_active and (not sub.expires_at or sub.expires_at > now) else "expired"

    return Response({
        "plan": sub.plan,
        "price":sub.price,
        "status": status_value,
        "expires_at": sub.expires_at
    })

@api_view(["POST"])
@permission_classes([IsAuthenticatedUser])
def change_subscription_plan(request):
    plan = request.data.get("plan")
    if plan not in ["FREE", "STARTER", "PRO", "PREMIUM"]:
        return Response({"error": "Invalid plan"}, status=400)

    user = request.user
    sub = getattr(user, "subscription", None)

    if not sub:
        sub = Subscription.objects.create(
            client=user,
            plan=plan,
            expires_at=timezone.now() + timedelta(days=30)
        )
        created = True
    else:
        sub.plan = plan
        sub.expires_at = timezone.now() + timedelta(days=30)
        sub.save()
        created = False

    # create a payment whenever plan is changed/created
    try:
        Payment.objects.create(
            client=user,
            subscription=sub,
            plan=sub.plan,
            amount=Decimal(getattr(sub, "price", 0) or 0),
            currency=getattr(sub, "currency", "XAF") or "XAF",
            gateway=getattr(sub, "gateway", None),
            gateway_subscription_id=getattr(sub, "gateway_subscription_id", None),
            status="success",
            paid_at=timezone.now()
        )
    except Exception:
        pass

    return Response({
        "detail": f"Plan updated to {plan}",
        "expires_at": sub.expires_at,
        "created": created
    })

@api_view(["POST"])
@permission_classes([IsAuthenticatedUser])
def toggle_subscription_status(request):
    user = request.user
    sub = getattr(user, "subscription", None)
    sub.is_active = not sub.is_active
    sub.save()
    return Response({"active": sub.is_active})

@api_view(["POST"])
@permission_classes([IsAuthenticatedUser])
def renew_subscription(request):
    user = request.user
    sub = getattr(user, "subscription", None)
    months = int(request.data.get("months", 1))
    # extend expiry date
    if not sub:
        sub = Subscription.objects.create(client=user)

    if sub.expires_at:
        sub.expires_at += timedelta(days=30 * months)
    else:
        sub.expires_at = timezone.now() + timedelta(days=30 * months)
    sub.is_active = True

    sub.save()

    # create a payment for the renewal (amount = price * months)
    try:
        price = getattr(sub, "price", 0) or 0
        Payment.objects.create(
            client=user,
            subscription=sub,
            plan=sub.plan,
            amount=Decimal(price) * Decimal(months),
            currency=getattr(sub, "currency", "XAF") or "XAF",
            gateway=getattr(sub, "gateway", None),
            gateway_subscription_id=getattr(sub, "gateway_subscription_id", None),
            status="success",
            paid_at=timezone.now()
        )
    except Exception:
        pass

    return Response({"detail": "Subscription renewed", "expires_at": sub.expires_at})