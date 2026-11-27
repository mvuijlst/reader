from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path

from apps.feeds.login_throttle import ThrottledAdminAuthenticationForm, ThrottledAuthenticationForm

# Both sign-in pages give up on an address after too many failed attempts.
admin.site.login_form = ThrottledAdminAuthenticationForm

urlpatterns = [
    path("admin/", admin.site.urls),
    path("login/", auth_views.LoginView.as_view(authentication_form=ThrottledAuthenticationForm), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("", include("apps.feeds.urls")),
]
