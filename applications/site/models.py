from tortoise import fields, models

class Terms(models.Model):
    id = fields.IntField(pk=True)
    title = fields.CharField(max_length=255)
    details = fields.TextField()
    updated_at = fields.DatetimeField(auto_now=True)

class Policy(models.Model):
    id = fields.IntField(pk=True)
    title = fields.CharField(max_length=255)
    details = fields.TextField()
    updated_at = fields.DatetimeField(auto_now=True)


class CookiesPolicy(models.Model):
    id = fields.IntField(pk=True)
    title = fields.CharField(max_length=255)
    details = fields.TextField()
    updated_at = fields.DatetimeField(auto_now=True)


class SiteReview(models.Model):
    id = fields.IntField(pk=True)
    user = fields.ForeignKeyField(
        "models.User",
        related_name="site_review",
        on_delete=fields.CASCADE,
        unique=True,
    )
    rating = fields.IntField()
    comment = fields.TextField()
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)
