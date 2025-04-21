from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone
import datetime

class AttendanceLog(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    timestamp = models.DateTimeField(auto_now_add=True)
    action = models.CharField(max_length=50)
    note = models.TextField(blank=True, null=True)  # Optional note field

    def __str__(self):
        return f"{self.user.username} - {self.action} at {self.timestamp}"

class DailyTimeAllocation(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    date = models.DateField()
    break1_minutes_used = models.IntegerField(default=0)  # Out of 15 minutes for first break
    break2_minutes_used = models.IntegerField(default=0)  # Out of 15 minutes for second break
    lunch_minutes_used = models.IntegerField(default=0)  # Out of 60 minutes
    break1_start_time = models.DateTimeField(null=True, blank=True)
    break2_start_time = models.DateTimeField(null=True, blank=True)
    lunch_start_time = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = ['user', 'date']

    def break1_minutes_remaining(self):
        return max(15 - self.break1_minutes_used, 0)
    
    def break2_minutes_remaining(self):
        return max(15 - self.break2_minutes_used, 0)
    
    def lunch_minutes_remaining(self):
        return max(60 - self.lunch_minutes_used, 0)  # 60 minutes for lunch

    def is_break1_exceeded(self):
        return self.break1_minutes_used > 15

    def is_break2_exceeded(self):
        return self.break2_minutes_used > 15

    def is_lunch_exceeded(self):
        return self.lunch_minutes_used > 60

    def break1_minutes_exceeded(self):
        if self.is_break1_exceeded():
            return self.break1_minutes_used - 15
        return 0

    def break2_minutes_exceeded(self):
        if self.is_break2_exceeded():
            return self.break2_minutes_used - 15
        return 0

    def lunch_minutes_exceeded(self):
        if self.is_lunch_exceeded():
            return self.lunch_minutes_used - 60
        return 0
        
    def total_break_minutes_remaining(self):
        return self.break1_minutes_remaining() + self.break2_minutes_remaining()
        
    def total_break_minutes_used(self):
        return self.break1_minutes_used + self.break2_minutes_used

    def __str__(self):
        return f"{self.user.username}'s time allocation for {self.date}"
