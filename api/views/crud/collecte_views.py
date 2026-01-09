from django.shortcuts import get_object_or_404
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from rest_framework import status
from django.utils import timezone
from datetime import datetime
from api.permissions import IsAuthenticatedUser
from api.models import Collecte, Subscription, User
from api.serializers import CollecteSerializer


@api_view(["POST"])
@permission_classes([IsAuthenticatedUser])
def create_collecte(request):
    """Only BOUNCER can create collecte."""
    user = request.user
    if user.role != "BOUNCER":
        return Response({"detail": "Only bouncers can create collectes"}, status=403)

    data = request.data.copy()
    
    # videur and client are read-only, so don't set them in data
    # They'll be passed to serializer.save() instead
    
    # client must be provided
    client_id = data.get("client") or data.get("client_id")
    
    if not client_id:
        return Response({"client": ["This field is required"]}, status=400)
    
    try:
        client = User.objects.get(pk=client_id, role="USER")
    except User.DoesNotExist:
        return Response({"client": ["User not found or not a client"]}, status=404)
    
    # get subscription from client (1-to-1 relationship)
    if not hasattr(client, 'subscription') or client.subscription is None:
        return Response({"detail": "Client does not have an active subscription"}, status=400)
    
    data["subscription_id"] = client.subscription.id
    # Don't set client/videur in data since they're read-only, pass them to save() instead
    
    serializer = CollecteSerializer(data=data)
    if serializer.is_valid():
        # Pass client and videur to save() since they're read-only fields
        collecte = serializer.save(client=client, videur=user)
        return Response(CollecteSerializer(collecte).data, status=201)
    return Response(serializer.errors, status=400)


@api_view(["GET"])
@permission_classes([IsAuthenticatedUser])
def get_collecte(request, collecte_id):
    """Get a single collecte by id. Permission: videur/client/admin."""
    user = request.user
    try:
        collecte = Collecte.objects.select_related('client', 'videur', 'subscription').get(pk=collecte_id)
    except Collecte.DoesNotExist:
        return Response({"detail": "Collecte not found"}, status=404)
    
    # permission: can view if videur, client, or admin
    is_allowed = (
        user.id == collecte.videur_id or
        user.id == collecte.client_id or
        user.role in ("SADMIN", "ADMIN")
    )
    if not is_allowed:
        return Response({"detail": "Forbidden"}, status=403)
    
    serializer = CollecteSerializer(collecte)
    return Response(serializer.data)


@api_view(["GET"])
@permission_classes([IsAuthenticatedUser])
def list_collectes(request):
    """List collectes with filters: client, videur, status, waste_type, date_from, date_to.
    Sorted descending by id.
    Permissions: bouncers see their own, admins see all, clients see their own.
    """
    user = request.user
    qs = Collecte.objects.select_related('client', 'videur', 'subscription').all()
    
    is_privileged = user.role in ("SADMIN", "ADMIN", "BOUNCER")
    
    # filters
    client_id = request.GET.get('client')
    videur_id = request.GET.get('videur')
    status_val = request.GET.get('status')
    waste_type = request.GET.get('waste_type')
    date_from = request.GET.get('date_from')
    date_to = request.GET.get('date_to')
    
    if client_id:
        if not is_privileged:
            return Response({"detail": "Forbidden"}, status=403)
        qs = qs.filter(client__id=client_id)
    else:
        if not is_privileged:
            qs = qs.filter(client=user)
    
    if videur_id:
        qs = qs.filter(videur__id=videur_id)
    
    if status_val:
        qs = qs.filter(status=status_val)
    
    if waste_type:
        qs = qs.filter(waste_type=waste_type)
    
    if date_from:
        try:
            dt = datetime.fromisoformat(date_from.replace('Z', '+00:00'))
            qs = qs.filter(date__gte=dt)
        except Exception:
            return Response({"date_from": ["Invalid ISO datetime format"]}, status=400)
    
    if date_to:
        try:
            dt = datetime.fromisoformat(date_to.replace('Z', '+00:00'))
            qs = qs.filter(date__lte=dt)
        except Exception:
            return Response({"date_to": ["Invalid ISO datetime format"]}, status=400)
    
    qs = qs.order_by('-id')
    
    serializer = CollecteSerializer(qs, many=True)
    return Response(serializer.data)


@api_view(["PUT", "PATCH"])
@permission_classes([IsAuthenticatedUser])
def update_collecte(request, collecte_id):
    """Update a collecte. Permission: videur (if assigned) or admin."""
    user = request.user
    try:
        collecte = Collecte.objects.select_related('client', 'videur', 'subscription').get(pk=collecte_id)
    except Collecte.DoesNotExist:
        return Response({"detail": "Collecte not found"}, status=404)
    
    # permission: videur or admin
    is_allowed = (
        user.role in ("SADMIN", "ADMIN") or
        (user.role == "BOUNCER" and user.id == collecte.videur_id)
    )
    if not is_allowed:
        return Response({"detail": "Forbidden"}, status=403)
    
    serializer = CollecteSerializer(collecte, data=request.data, partial=True)
    if serializer.is_valid():
        serializer.save()
        return Response(serializer.data)
    return Response(serializer.errors, status=400)


@api_view(["DELETE"])
@permission_classes([IsAuthenticatedUser])
def delete_collecte(request, collecte_id):
    """Delete a collecte. Permission: videur (if assigned) or admin."""
    user = request.user
    try:
        collecte = Collecte.objects.get(pk=collecte_id)
    except Collecte.DoesNotExist:
        return Response({"detail": "Collecte not found"}, status=404)
    
    # permission: videur or admin
    is_allowed = (
        user.role in ("SADMIN", "ADMIN") or
        (user.role == "BOUNCER" and user.id == collecte.videur_id)
    )
    if not is_allowed:
        return Response({"detail": "Forbidden"}, status=403)
    
    collecte.delete()
    return Response({"detail": "Collecte deleted"})
