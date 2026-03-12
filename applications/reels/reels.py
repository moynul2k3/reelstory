from tortoise import fields, models


class Reel(models.Model):
    id = fields.IntField(pk=True)
    title = fields.CharField(max_length=255)

    # "Type" dropdown from UI (Casino, Poker, etc.)
    category = fields.ForeignKeyField(
        "models.Category",
        related_name="reels",
        null=True,
    )

    bonuses = fields.IntField(default=0)
    short_description = fields.TextField(null=True)
    terms_highlights = fields.TextField(null=True)
    affiliate_link = fields.CharField(max_length=1000, null=True)
    languages = fields.CharField(max_length=50, default="en")
    tags = fields.JSONField(default=list)
    disclaimers = fields.TextField(null=True)

    media_file = fields.CharField(max_length=500, null=True)
    thumbnail = fields.CharField(max_length=500, null=True)
    logo = fields.CharField(max_length=500, null=True)

    is_adult_content = fields.BooleanField(default=False)
    is_active = fields.BooleanField(default=True)
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = "reels"
        ordering = ["-created_at"]

    def __str__(self):
        return self.title
