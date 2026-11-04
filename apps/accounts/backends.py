from django.contrib.auth.backends import ModelBackend
from django.core.cache import cache
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from .models import User

USER_CACHE_TTL = 60


def _key(user_id):
    return f"accounts:user:{user_id}"


class CachedModelBackend(ModelBackend):
    """The normal email/password backend, except that the signed-in user
    is kept in the cache for a minute instead of being read from the
    database on every page. Saving or deleting a user clears it on this
    server; other servers pick the change up within the minute."""

    def get_user(self, user_id):
        user = cache.get(_key(user_id))
        if user is None:
            user = super().get_user(user_id)
            if user is not None:
                cache.set(_key(user_id), user, USER_CACHE_TTL)
        return user


@receiver(post_save, sender=User)
@receiver(post_delete, sender=User)
def _forget_cached_user(sender, instance, **kwargs):
    cache.delete(_key(instance.pk))
