from django.shortcuts import get_object_or_404
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from django.utils import timezone
from rest_framework import status
from django.db.models import Count
from django.db.models import Q
from api.permissions import IsAuthenticatedUser
from api.serializers import UserMeSerializer, UserSerializer
from api.services.notify import create_and_send_whatsapp_notification
from django.utils.text import slugify
from django.db import transaction


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