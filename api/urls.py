from django.urls import path
from api.views.auth.auth_views import change_subscription_plan, check_subscription_status, delete_subscription, get_church_subscription, renew_subscription, send_otp_view, toggle_subscription_status, update_subscription, verify_otp_view
from api.views.crud.crud_views import delete_self, get_current_user, update_self, create_schedule, get_schedule, update_schedule, delete_schedule, list_schedules
urlpatterns = [
    path("auth/send-otp/", send_otp_view),
    path("auth/verify-otp/", verify_otp_view),
    path("user/me/update/", update_self),
    path("user/me/delete/", delete_self),
    path("user/me/",get_current_user ),
    path("subscription/", get_church_subscription),
    path("subscription/update/", update_subscription),
    path("subscription/delete/", delete_subscription),
    path("subscription/status/", check_subscription_status),
    path("subscription/change-plan/", change_subscription_plan),
    path("subscription/toggle/", toggle_subscription_status),
    path("subscription/renew/", renew_subscription),
    # Schedule endpoints
    path("schedule/", get_schedule),
    path("schedule/create/", create_schedule),
    path("schedule/update/", update_schedule),
    path("schedule/delete/", delete_schedule),
    path("schedules/", list_schedules),
]
