# api/services/notify.py
from api.models import Notification
from django.utils import timezone

from api.services.whatsapp import send_whatsapp_template

def create_and_send_whatsapp_notification(user,title, message, template_name=None, template_params=None,message_eng="",title_eng=""):
    notif = Notification.objects.create(
        user=user,
        title=title,
        eng_title=title_eng,
        message=message,
        eng_message=message_eng,
        type="SUCCESS",
        channel="WHATSAPP"
    )

    response_meta = None
    try:
        if template_name:
            response_meta = send_whatsapp_template(user.phone_number, template_name, template_params or [])
        else:
            # fallback: use text message (for dev/testing) — note: templates recommended for production
            response_meta = {"info": "no_template_used", "message": message}

        notif.mark_sent(response_meta)
    except Exception as e:
        notif.meta = {"error": str(e)}
        notif.save()
    return notif
