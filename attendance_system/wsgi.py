import os
from django.core.wsgi import get_wsgi_application

# Set the default settings module for the 'attendance_system' project.
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'attendance_system.settings')

application = get_wsgi_application()
