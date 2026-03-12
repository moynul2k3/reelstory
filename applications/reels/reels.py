from tortoise import fields, models
from tortoise.validators import MinValueValidator, MaxValueValidator


class Reel(models.Model):
    id = fields.IntField(pk=True)
    title = fields.CharField(max_length=255)
    category = fields.ForeignKeyField(
        "models.Category",
        related_name="reels",
        null=True,
    )
    viewers = fields.ManyToManyField(
        "models.User",
        related_name="viewed_reels",
        through="reels_viewers",
    )

    bonuses = fields.IntField(default=0)
    share_count = fields.IntField(default=0)
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




class ReelsReview(models.Model):
    id = fields.IntField(pk=True)
    reel = fields.ForeignKeyField("models.Reel", related_name="reel", on_delete=fields.CASCADE)
    user = fields.ForeignKeyField('models.User', on_delete=fields.CASCADE, related_name='reviewer')  # changed
    rating = fields.IntField(validators=[MinValueValidator(1), MaxValueValidator(5)], null=True)
    review = fields.TextField(null=True)
    parent = fields.ForeignKeyField('models.ReelsReview', null=True, related_name="parent_review", on_delete=fields.CASCADE)
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)


    class Meta:
        ordering = ["-created_at"]

    @property
    def is_reply(self) -> bool:
        return self.parent_id is not None

    def __str__(self):
        return f"Review of Reel {self.reel_id} by User {self.user_id}"
