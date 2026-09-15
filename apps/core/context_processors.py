from .models import Brand


def brand(request):
    """Makes the single Brand record available to every template as
    {{ brand }}, since no User points at it anymore."""
    return {"brand": Brand.objects.first()}
