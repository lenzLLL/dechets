from django.shortcuts import get_object_or_404
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from django.utils import timezone
from rest_framework import status
from django.db.models import Count
from django.db.models import Q
from django.db.models import Sum
from api.permissions import IsAuthenticatedUser
from api.serializers import UserMeSerializer, UserSerializer, PaymentSerializer, SubscriptionSerializer
from api.services.notify import create_and_send_whatsapp_notification
from django.utils.text import slugify
from django.db import transaction
from api.models import Payment, Schedule, Subscription, User
from api.serializers import ScheduleSerializer

from datetime import datetime


@api_view(["PUT", "PATCH"])
@permission_classes([IsAuthenticatedUser])
def update_self(request):
    user = request.user
    serializer = UserSerializer(user, data=request.data, partial=True)
    if serializer.is_valid():
        serializer.save()
        return Response(serializer.data)
    return Response(serializer.errors, status=400)

@api_view(["DELETE"])
@permission_classes([IsAuthenticatedUser])
def delete_self(request):
    user = request.user
    user.delete()
    return Response({"detail": "Your account has been deleted"})

@api_view(["GET"])
@permission_classes([IsAuthenticatedUser])
def get_current_user(request):
    serializer = UserMeSerializer(request.user)
    return Response(serializer.data)


@api_view(["POST"])
@permission_classes([IsAuthenticatedUser])
def create_schedule(request):
    user = request.user
    # only admins or bouncers can create schedules
    if user.role  in ("SADMIN", "ADMIN", "BOUNCER"):
        return Response({"detail": "Forbidden"}, status=403)

    data = request.data.copy()
    # prefer explicit subscription id, else accept user/user_id to resolve subscription
    sub_id = data.get("subscription") or data.get("subscription_id")
    user_id = data.get("user") or data.get("user_id")

    sub = None
    if sub_id:
        try:
            sub = Subscription.objects.get(pk=sub_id)
        except Subscription.DoesNotExist:
            return Response({"subscription": ["Subscription not found."]}, status=404)
    elif user_id:
        try:
            sub = Subscription.objects.get(client__id=user_id)
        except Subscription.DoesNotExist:
            return Response({"subscription": ["Subscription for given user not found."]}, status=404)
    else:
        # fallback: if creator is bouncer/admin, require explicit id; if creator has own subscription, use it
        sub = getattr(user, "subscription", None)
        if not sub:
            return Response({"subscription": ["Provide subscription id or user id"]}, status=400)

    # prevent duplicate schedule
    if hasattr(sub, "schedule") and sub.schedule:
        return Response({"error": "Schedule already exists for this subscription"}, status=400)

    # if creator is bouncer, assign themselves as videur unless explicitly set otherwise
    if user.role == "BOUNCER" and not data.get("videur"):
        data["videur"] = user.id

    data["subscription"] = sub.id
    serializer = ScheduleSerializer(data=data)
    if serializer.is_valid():
        schedule = serializer.save()
        return Response(ScheduleSerializer(schedule).data, status=201)
    return Response(serializer.errors, status=400)


@api_view(["GET"])
@permission_classes([IsAuthenticatedUser])
def get_schedule(request):
    user = request.user
    # admins/bouncers can request schedule for any subscription via ?subscription=<id>
    sub_id = request.GET.get("subscription")
    user_id = request.GET.get("user") or request.GET.get("user_id")
    if sub_id and user.role in ("SADMIN", "ADMIN", "BOUNCER"):
        try:
            sub = Subscription.objects.get(pk=sub_id)
        except Subscription.DoesNotExist:
            return Response({"detail": "Subscription not found"}, status=404)
    elif user_id and user.role in ("SADMIN", "ADMIN", "BOUNCER"):
        try:
            sub = Subscription.objects.get(client__id=user_id)
        except Subscription.DoesNotExist:
            return Response({"detail": "Subscription not found for provided user"}, status=404)
    else:
        sub = getattr(user, "subscription", None)

    if not sub or not hasattr(sub, "schedule") or not sub.schedule:
        return Response({"detail": "No schedule found"}, status=404)
    serializer = ScheduleSerializer(sub.schedule)
    return Response(serializer.data)


@api_view(["GET"])
@permission_classes([IsAuthenticatedUser])
def list_schedules(request):
    """List schedules with optional filters:
    ?videur=<id>&city=<name>&day=<1..7 or name>&time_from=HH:MM&time_to=HH:MM&user=<client_id>
    Returns schedules ordered descending by id.
    """
    user = request.user
    qs = Schedule.objects.select_related('subscription', 'videur').all()

    # permissions: non-admin/non-bouncer can only see their own subscription schedule
    is_privileged = user.role in ("SADMIN", "ADMIN", "BOUNCER")

    videur_id = request.GET.get('videur')
    city = request.GET.get('city')
    day = request.GET.get('day')
    time_from = request.GET.get('time_from')
    time_to = request.GET.get('time_to')
    user_id = request.GET.get('user')

    if videur_id:
        qs = qs.filter(videur__id=videur_id)

    if city:
        qs = qs.filter(subscription__city__iexact=city)

    if user_id:
        # privileged only
        if not is_privileged:
            return Response({"detail": "Forbidden"}, status=403)
        qs = qs.filter(subscription__client__id=user_id)
    else:
        if not is_privileged:
            # non privileged: restrict to request user's subscription only
            qs = qs.filter(subscription__client=user)

    # preliminary order (descending by id)
    qs = qs.order_by('-id')

    # day/time filtering performed in Python because slots is JSONField list
    def slot_matches(slot, wanted_day=None, t_from=None, t_to=None):
        try:
            s_day = slot.get('day')
            s_time = slot.get('time')
        except Exception:
            return False
        # normalize day (accept int as string too)
        DAY_MAP = { '1':'Monday','2':'Tuesday','3':'Wednesday','4':'Thursday','5':'Friday','6':'Saturday','7':'Sunday' }
        if wanted_day:
            wanted = wanted_day
            if isinstance(wanted, str) and wanted.isdigit():
                wanted = DAY_MAP.get(wanted)
            if isinstance(wanted, int):
                wanted = DAY_MAP.get(str(wanted))
            if not wanted:
                wanted = wanted_day
            if s_day != wanted:
                return False
        if t_from or t_to:
            try:
                st = datetime.strptime(s_time, '%H:%M').time()
            except Exception:
                return False
            if t_from:
                if st < t_from:
                    return False
            if t_to:
                if st > t_to:
                    return False
        return True

    # parse time bounds
    t_from_obj = None
    t_to_obj = None
    def parse_time_str(s, field_name):
        if not s:
            return None, None
        s = s.strip()
        # strip possible surrounding quotes
        if (s.startswith('"') and s.endswith('"')) or (s.startswith("'") and s.endswith("'")):
            s = s[1:-1].strip()
        try:
            return datetime.strptime(s, '%H:%M').time(), None
        except Exception:
            return None, {field_name: ["Invalid time format, use HH:MM (00:00-23:59)"]}

    if time_from:
        t_from_obj, err = parse_time_str(time_from, 'time_from')
        if err:
            return Response(err, status=400)
    if time_to:
        t_to_obj, err = parse_time_str(time_to, 'time_to')
        if err:
            return Response(err, status=400)

    wanted_day = None
    if day:
        if day.isdigit():
            DAY_MAP = { '1':'Monday','2':'Tuesday','3':'Wednesday','4':'Thursday','5':'Friday','6':'Saturday','7':'Sunday' }
            wanted_day = DAY_MAP.get(day)
            if not wanted_day:
                return Response({"day": ["Invalid day integer, use 1..7"]}, status=400)
        else:
            wanted_day = day.strip().capitalize()

    results = []
    for sched in qs:
        slots = sched.slots or []
        if day or time_from or time_to:
            matched = False
            for slot in slots:
                if slot_matches(slot, wanted_day=wanted_day, t_from=t_from_obj, t_to=t_to_obj):
                    matched = True
                    break
            if not matched:
                continue
        results.append(sched)

    serializer = ScheduleSerializer(results, many=True)
    data = serializer.data

    # if day/time filters were applied, trim returned slots to only matching ones
    if day or time_from or time_to:
        trimmed = []
        for item, sched in zip(data, results):
            filtered_slots = []
            for slot in item.get('slots', []):
                if slot_matches(slot, wanted_day=wanted_day, t_from=t_from_obj, t_to=t_to_obj):
                    filtered_slots.append(slot)
            if filtered_slots:
                item['slots'] = filtered_slots
                trimmed.append(item)
        return Response(trimmed)

    return Response(data)


@api_view(["PUT", "PATCH"])
@permission_classes([IsAuthenticatedUser])
def update_schedule(request):
    user = request.user
    # find schedule either by user's subscription or by provided subscription id (admins/bouncers)
    sub_id = request.data.get("subscription") or request.GET.get("subscription")
    user_id = request.data.get("user") or request.data.get("user_id") or request.GET.get("user") or request.GET.get("user_id")
    sub = None
    if sub_id and user.role in ("SADMIN", "ADMIN", "BOUNCER"):
        try:
            sub = Subscription.objects.get(pk=sub_id)
        except Subscription.DoesNotExist:
            return Response({"detail": "Subscription not found"}, status=404)
    elif user_id and user.role in ("SADMIN", "ADMIN", "BOUNCER"):
        try:
            sub = Subscription.objects.get(client__id=user_id)
        except Subscription.DoesNotExist:
            return Response({"detail": "Subscription not found"}, status=404)
    else:
        sub = getattr(user, "subscription", None)

    if not sub or not hasattr(sub, "schedule") or not sub.schedule:
        return Response({"detail": "No schedule to update"}, status=404)
    schedule = sub.schedule

    # permission: admins can update any; bouncers only their assigned schedule
    if user.role in ("SADMIN", "ADMIN"):
        pass
    elif user.role == "BOUNCER":
        # allow bouncer to modify only if they are assigned or if no videur assigned yet
        if schedule.videur_id is not None and schedule.videur_id != user.id:
            return Response({"detail": "Forbidden"}, status=403)
    else:
        return Response({"detail": "Forbidden"}, status=403)

    serializer = ScheduleSerializer(schedule, data=request.data, partial=True)
    if serializer.is_valid():
        serializer.save()
        return Response(serializer.data)
    return Response(serializer.errors, status=400)


@api_view(["DELETE"])
@permission_classes([IsAuthenticatedUser])
def delete_schedule(request):
    user = request.user
    sub_id = request.data.get("subscription") or request.GET.get("subscription")
    user_id = request.data.get("user") or request.data.get("user_id") or request.GET.get("user") or request.GET.get("user_id")
    sub = None
    if sub_id and user.role in ("SADMIN", "ADMIN", "BOUNCER"):
        try:
            sub = Subscription.objects.get(pk=sub_id)
        except Subscription.DoesNotExist:
            return Response({"detail": "Subscription not found"}, status=404)
    elif user_id and user.role in ("SADMIN", "ADMIN", "BOUNCER"):
        try:
            sub = Subscription.objects.get(client__id=user_id)
        except Subscription.DoesNotExist:
            return Response({"detail": "Subscription not found"}, status=404)
    else:
        sub = getattr(user, "subscription", None)

    if not sub or not hasattr(sub, "schedule") or not sub.schedule:
        return Response({"detail": "No schedule to delete"}, status=404)

    schedule = sub.schedule
    # permission: admins can delete any; bouncers only their assigned schedule
    if user.role in ("SADMIN", "ADMIN"):
        pass
    elif user.role == "BOUNCER":
        # allow bouncer to delete only if they are assigned or if no videur assigned yet
        if schedule.videur_id is not None and schedule.videur_id != user.id:
            return Response({"detail": "Forbidden"}, status=403)
    else:
        return Response({"detail": "Forbidden"}, status=403)

    schedule.delete()
    return Response({"detail": "Schedule deleted"})


@api_view(["GET"])
@permission_classes([IsAuthenticatedUser])
def list_users(request):
    """List users with optional filters: ?role=&city=&address=&subscription=PLAN
    Results ordered descending by id.
    """
    qs = User.objects.all()
    role = request.GET.get('role')
    city = request.GET.get('city')
    address = request.GET.get('address')
    subscription_plan = request.GET.get('subscription')

    if role:
        qs = qs.filter(role__iexact=role)
    if city:
        qs = qs.filter(city__icontains=city)
    if address:
        qs = qs.filter(address__icontains=address)
    if subscription_plan:
        qs = qs.filter(subscription__plan__iexact=subscription_plan)

    qs = qs.order_by('-id')
    serializer = UserSerializer(qs, many=True)
    return Response(serializer.data)


@api_view(["GET"])
@permission_classes([IsAuthenticatedUser])
def list_payments(request):
    """List payments with filters: ?client=&subscription=&status=&plan=. Ordered desc by created_at."""
    qs = Payment.objects.select_related('client', 'subscription').all()
    client_id = request.GET.get('client')
    sub_id = request.GET.get('subscription')
    status_val = request.GET.get('status')
    plan = request.GET.get('plan')

    if client_id:
        qs = qs.filter(client__id=client_id)
    if sub_id:
        qs = qs.filter(subscription__id=sub_id)
    if status_val:
        qs = qs.filter(status__iexact=status_val)
    if plan:
        qs = qs.filter(plan__iexact=plan)

    qs = qs.order_by('-created_at')
    serializer = PaymentSerializer(qs, many=True)
    return Response(serializer.data)


@api_view(["GET"])
@permission_classes([IsAuthenticatedUser])
def list_subscriptions(request):
    """List subscriptions with optional filters: ?client=&plan=&city=. Ordered desc by started_at."""
    qs = Subscription.objects.select_related('client').all()
    client_id = request.GET.get('client')
    plan = request.GET.get('plan')
    city = request.GET.get('city')

    if client_id:
        qs = qs.filter(client__id=client_id)
    if plan:
        qs = qs.filter(plan__iexact=plan)
    if city:
        qs = qs.filter(city__icontains=city)

    qs = qs.order_by('-started_at')
    serializer = SubscriptionSerializer(qs, many=True)
    return Response(serializer.data)


@api_view(["GET"])
@permission_classes([IsAuthenticatedUser])
def stats_revenues(request):
    """Return revenue totals: daily, monthly, yearly, total (only successful payments).
    Restricted to ADMIN/SADMIN.
    """
    user = request.user
    if user.role not in ("SADMIN", "ADMIN"):
        return Response({"detail": "Forbidden"}, status=403)

    now = timezone.now()
    # daily: paid_at date == today
    daily_qs = Payment.objects.filter(status='success', paid_at__date=now.date())
    monthly_qs = Payment.objects.filter(status='success', paid_at__year=now.year, paid_at__month=now.month)
    yearly_qs = Payment.objects.filter(status='success', paid_at__year=now.year)
    total_qs = Payment.objects.filter(status='success')

    daily_total = daily_qs.aggregate(total=Sum('amount'))['total'] or 0
    monthly_total = monthly_qs.aggregate(total=Sum('amount'))['total'] or 0
    yearly_total = yearly_qs.aggregate(total=Sum('amount'))['total'] or 0
    total_all = total_qs.aggregate(total=Sum('amount'))['total'] or 0

    return Response({
        'daily': str(daily_total),
        'monthly': str(monthly_total),
        'yearly': str(yearly_total),
        'total': str(total_all),
        'currency': getattr(Payment.objects.first(), 'currency', 'XAF')
    })


@api_view(["GET"])
@permission_classes([IsAuthenticatedUser])
def stats_subscriptions(request):
    """Return subscription counts: daily, monthly, yearly, total.
    Restricted to ADMIN/SADMIN.
    """
    user = request.user
    if user.role in ("SADMIN", "ADMIN"):
        return Response({"detail": "Forbidden"}, status=403)

    now = timezone.now()
    daily_count = Subscription.objects.filter(started_at__date=now.date()).count()
    monthly_count = Subscription.objects.filter(started_at__year=now.year, started_at__month=now.month).count()
    yearly_count = Subscription.objects.filter(started_at__year=now.year).count()
    total_count = Subscription.objects.all().count()

    # also provide breakdown by plan
    by_plan = Subscription.objects.values('plan').annotate(count=Count('id')).order_by('-count')

    return Response({
        'daily': daily_count,
        'monthly': monthly_count,
        'yearly': yearly_count,
        'total': total_count,
        'by_plan': list(by_plan)
    })