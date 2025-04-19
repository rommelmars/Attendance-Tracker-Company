from django.urls import path
from . import views

urlpatterns = [
    path('', views.login_view, name='login'),  # 👈 Homepage now shows login page
    path('tracker/', views.tracker_view, name='tracker'),  # 👈 After login, redirect here
    path('logout/', views.logout_view, name='logout'),
    path('dashboard/', views.dashboard, name='dashboard'),  # optional: normal user dashboard
    path('admin-dashboard/', views.admin_dashboard, name='admin_dashboard'),
    path('export-csv/', views.export_csv, name='export_csv'),
    path('admin-export-csv/', views.admin_export_csv, name='admin_export_csv'),
]
