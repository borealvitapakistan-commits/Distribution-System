"""
URL configuration for config project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path


urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("django.contrib.auth.urls")),
    path("users/", include("apps.accounts.urls")),
    path("audit/", include("apps.audit.urls")),
    path("api/v1/auth/", include("apps.accounts.api_urls")),
    path("api/v1/", include("apps.core.api_urls")),
    path("api/v1/", include("apps.audit.api_urls")),
    path("api/v1/", include("apps.owners.api_urls")),
    path("api/v1/", include("apps.distributors.api_urls")),
    path("api/v1/", include("apps.manufacturers.api_urls")),
    path("api/v1/", include("apps.products.api_urls")),
    path("api/v1/", include("apps.owner_warehouse.api_urls")),
    path("api/v1/", include("apps.owner_inventory.api_urls")),
    path("api/v1/", include("apps.distributor_inventory.api_urls")),
    path("api/v1/", include("apps.requests.api_urls")),

    path("", include("apps.core.urls")),
    path("", include("apps.owners.urls")),
    path("", include("apps.distributors.urls")),
    path("", include("apps.manufacturers.urls")),
    path("", include("apps.products.urls")),
    path("", include("apps.owner_warehouse.urls")),
    path("", include("apps.owner_inventory.urls")),
    path("", include("apps.distributor_warehouse.urls")),
    path("", include("apps.distributor_inventory.urls")),
    path("", include("apps.requests.urls")),
    path("", include("apps.finance.urls")),
]


if settings.DEBUG:
    urlpatterns += static(
        settings.MEDIA_URL,
        document_root=settings.MEDIA_ROOT,
    )




