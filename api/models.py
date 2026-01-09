from django.db import models
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin,Group, Permission
from django.utils import timezone
from django.utils.text import slugify
import uuid
from django.conf import settings

class UserManager(BaseUserManager):
    def create_user(self, phone_number, **extra_fields):
        if not phone_number:
            raise ValueError("Le numéro de téléphone doit être fourni")

        user = self.model(phone_number=phone_number, **extra_fields)
        user.set_unusable_password()   # pas de mot de passe pour OTP WhatsApp
        user.save(using=self._db)
        return user

    def create_superuser(self, phone_number, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)

        if extra_fields.get("is_staff") is not True:
            raise ValueError("Superuser must have is_staff=True")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_superuser=True")

        return self.create_user(phone_number, **extra_fields)

class User(AbstractBaseUser, PermissionsMixin):
    ROLE_CHOICES = [
        ("SADMIN", "Admin"),
        ("USER", "Client"),
        ("ADMIN", "Admin"),
        ("BOUNCER", "Videur"),
    ]

    phone_number = models.CharField(max_length=50, unique=True, db_index=True)
    name = models.CharField(max_length=100, blank=True)
    picture_url = models.URLField(blank=True, null=True)
    zipcode = models.CharField(max_length=20, blank=True)
    address = models.CharField(max_length=255, blank=True, default="")
    city = models.CharField(max_length=255, blank=True, default="")
    country = models.CharField(max_length=100, blank=True, default="")
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default="USER", db_index=True)

    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    USERNAME_FIELD = "phone_number"
    REQUIRED_FIELDS = []
    groups = models.ManyToManyField(
        Group,
        related_name="custom_user_set",  # <- unique
        blank=True
    )
    user_permissions = models.ManyToManyField(
        Permission,
        related_name="custom_user_permissions_set",  # <- unique
        blank=True
    )
    objects = UserManager()

    def __str__(self):
        return f"{self.phone_number} ({self.role})"

    # helpers
    def is_client(self):
        return self.role == "USER"

    def is_videur(self):
        return self.role == "BOUNCER"

    def is_admin(self):
        return self.role == "SADMIN"
      
class OTP(models.Model):
    phone = models.CharField(max_length=20, unique=True)
    otp = models.CharField(max_length=6)
    created_at = models.DateTimeField(auto_now_add=True)
    last_sent_at = models.DateTimeField(auto_now=True)
    session_id = models.UUIDField(default=uuid.uuid4)

    def is_expired(self):
        expiration = settings.OTP_EXPIRATION_SECONDS
        print("last_sent_at:", settings.OTP_EXPIRATION_SECONDS)
        return (timezone.now() - self.last_sent_at).total_seconds() > expiration

    def can_resend(self):
        cooldown = settings.OTP_SEND_COOLDOWN_SECONDS
        return (timezone.now() - self.last_sent_at).total_seconds() > cooldown

class Notification(models.Model):
    NOTIF_TYPES = [
        ('OTP', 'Code OTP'),
        ('ACCOUNT_APPROVED', 'Compte activé'),
        ('INFO', 'Information'),
        ('WARNING', 'Avertissement'),
        ('ERROR', 'Erreur'),
        ("SUCCESS","Success")
    ]

    CHANNEL_CHOICES = [
        ("IN_APP", "In App"),
        ("WHATSAPP", "WhatsApp"),
        ("EMAIL", "Email")
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="notifications")
    title = models.CharField(max_length=255)
    message = models.TextField()
    eng_message = models.TextField(default="")
    eng_title = models.TextField(default="")
    type = models.CharField(max_length=20, choices=NOTIF_TYPES)
    channel = models.CharField(max_length=20, choices=CHANNEL_CHOICES, default="IN_APP")
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    sent = models.BooleanField(default=False)
    sent_at = models.DateTimeField(null=True, blank=True)
    meta = models.JSONField(default=dict, blank=True)  # store payload / gateway response

    def mark_sent(self, response_meta=None):
        self.sent = True
        self.sent_at = timezone.now()
        if response_meta:
            self.meta = response_meta
        self.save()

    def __str__(self):
        # safer if user might not have phone_number set
        phone = getattr(self.user, "phone_number", str(self.user.pk))
        return f"{phone} • {self.title}"

class Subscription(models.Model):
    PLAN_CHOICES = [
        ("FREE", "Gratuit"),
        ("STARTER", "Basique"),
        ("PRO", "Intermédiaire"),
        ("PREMIUM", "Premium"),
    ]

    client = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="subscription"
    )
    plan = models.CharField(max_length=30, choices=PLAN_CHOICES, default="FREE")
    started_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    collection_frequency = models.IntegerField(default=1)  # collectes par semaine
    longitude = models.FloatField(default=0)
    latitude = models.FloatField(default=0)
    address = models.CharField(max_length=255,default="")
    city = models.CharField(max_length=255,default="")
    # Paiement
    gateway = models.CharField(max_length=50, blank=True, null=True)
    gateway_subscription_id = models.CharField(max_length=200, blank=True, null=True)
    price = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    currency = models.CharField(max_length=10, default='XAF')

    def save(self, *args, **kwargs):
        PLAN_FREQUENCY = {
            "FREE": 1,
            "STARTER": 1,
            "PRO": 2,
            "PREMIUM": 7,
        }
        if not self.collection_frequency:
            self.collection_frequency = PLAN_FREQUENCY.get(self.plan, 1)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.client.phone_number} - {self.plan}"

    class Meta:
        verbose_name = "Abonnement"
        verbose_name_plural = "Abonnements"

# -------------------------
# Collecte / Tournee
# -------------------------
class Collecte(models.Model):
    STATUS_CHOICES = [
        ('scheduled', 'Planifiée'),
        ('in_progress', 'En cours'),
        ('completed', 'Terminée'),
        ('missed', 'Manquée')
    ]

    WASTE_CHOICES = [
        ('organic', 'Organique'),
        ('plastic', 'Plastique'),
        ('paper', 'Papier'),
        ('mixed', 'Mixte')
    ]

    client = models.ForeignKey(User, on_delete=models.CASCADE, limit_choices_to={'role':'USER'},related_name="collectes_client")
    videur = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, limit_choices_to={'role':'BOUNCER'},related_name="collectes_videur")
    subscription = models.ForeignKey(Subscription, on_delete=models.CASCADE)
    date = models.DateTimeField(default=timezone.now)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='scheduled')
    waste_type = models.CharField(max_length=50, choices=WASTE_CHOICES, default='mixed')
    weight_kg = models.FloatField(default=0)
    created_at = models.DateTimeField(default=timezone.now)


    def __str__(self):
        return f"{self.client.phone_number} - {self.date.date()} - {self.status}"

# -------------------------
# Payment Model
# -------------------------
class Payment(models.Model):
    STATUS_CHOICES = [
        ('pending', 'En attente'),
        ('success', 'Réussi'),
        ('failed', 'Échoué')
    ]

    # Liens
    client = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="user_payments"  # ⚠️ unique pour éviter les conflits
    )
    subscription = models.ForeignKey(
        'Subscription',
       on_delete=models.SET_NULL, null=True,
        related_name="payments"  # ⚠️ unique pour éviter les conflits
    )

    # Snapshot of the plan at time of payment (useful in history)
    plan = models.CharField(max_length=30, choices=Subscription.PLAN_CHOICES, default="FREE")

    # Infos paiement
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=10, default='XAF')
    gateway = models.CharField(max_length=50, blank=True, null=True)
    gateway_subscription_id = models.CharField(max_length=200, blank=True, null=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    paid_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.client.phone_number} - {self.amount} {self.currency} - {self.status}"

class Schedule(models.Model):
    subscription = models.OneToOneField(
        Subscription,
        on_delete=models.CASCADE,
        related_name="schedule"
    )
    videur = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        limit_choices_to={'role':'BOUNCER'},
        related_name="assigned_schedules"
    )
    # exemple : [("Lundi", "12:00"), ("Jeudi", "13:50")]
    # on peut stocker ça sous forme JSON
    slots = models.JSONField(default=list, help_text="Liste de jours et heures, ex: [{'day':'Monday','time':'12:00'}]")

    def __str__(self):
        return f"{self.subscription.client.phone_number} - {self.videur.phone_number if self.videur else 'Non assigné'}"