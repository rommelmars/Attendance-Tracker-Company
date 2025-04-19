from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required, user_passes_test
from django.http import HttpResponse
from .models import AttendanceLog, DailyTimeAllocation
from django.utils import timezone
import csv
from datetime import date, datetime, timedelta
from django.db.models import Q
from django.contrib import messages
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger

# 👤 LOGIN
def login_view(request):
    print("Login view accessed")  # Debug print
    
    if request.user.is_authenticated:
        print(f"User {request.user.username} is already authenticated, redirecting to tracker")  # Debug print
        return redirect('tracker')

    if request.method == 'POST':
        username = request.POST['username']
        password = request.POST['password']
        print(f"Login attempt for user: {username}")  # Debug print
        
        user = authenticate(request, username=username, password=password)
        if user:
            print(f"User {username} authenticated successfully")  # Debug print
            login(request, user)
            return redirect('tracker')
        else:
            print(f"Authentication failed for user: {username}")  # Debug print
            return render(request, 'login.html', {'error': 'Invalid credentials'})

    return render(request, 'login.html')

# 🚪 LOGOUT
def logout_view(request):
    logout(request)
    return redirect('login')

# 🧍 EMPLOYEE VIEW
@login_required
def tracker_view(request):
    # Check if a specific date was requested
    requested_date = request.GET.get('date')
    
    if requested_date:
        try:
            selected_date = datetime.strptime(requested_date, '%Y-%m-%d').date()
            today = selected_date
            is_today = today == date.today()
        except ValueError:
            selected_date = None
            today = date.today()
            is_today = True
    else:
        selected_date = None
        today = date.today()
        is_today = True
    
    # Get or create daily time allocation for today
    time_allocation, created = DailyTimeAllocation.objects.get_or_create(
        user=request.user,
        date=today,
    )
    
    # Retrieve logs for the user on the selected date
    logs = AttendanceLog.objects.filter(user=request.user, timestamp__date=today).order_by('-timestamp')
    
    # Pagination
    page = request.GET.get('page', 1)
    paginator = Paginator(logs, 10)  # Show 10 activities per page
    
    try:
        page_obj = paginator.page(page)
    except PageNotAnInteger:
        page_obj = paginator.page(1)
    except EmptyPage:
        page_obj = paginator.page(paginator.num_pages)
    
    # Calculate current status
    current_status = 'idle'
    ongoing_break = False
    ongoing_lunch = False
    
    # Check if there's an ongoing break or lunch
    if time_allocation.break_start_time and not time_allocation.break_start_time + timedelta(minutes=30) < timezone.now():
        ongoing_break = True
        current_status = 'on_break'
    
    if time_allocation.lunch_start_time and not time_allocation.lunch_start_time + timedelta(minutes=60) < timezone.now():
        ongoing_lunch = True
        current_status = 'on_lunch'

    # Check if user is clocked in today
    is_clocked_in = False
    clocked_in_today = AttendanceLog.objects.filter(
        user=request.user, 
        action='clock_in',
        timestamp__date=today
    ).exists()
    
    clocked_out_today = AttendanceLog.objects.filter(
        user=request.user, 
        action='clock_out',
        timestamp__date=today
    ).exists()
    
    # If the user has clocked in but not clocked out, they are considered clocked in
    if clocked_in_today:
        # Count clock ins and clock outs to determine current status
        clock_ins = AttendanceLog.objects.filter(
            user=request.user,
            action='clock_in',
            timestamp__date=today
        ).count()
        
        clock_outs = AttendanceLog.objects.filter(
            user=request.user,
            action='clock_out',
            timestamp__date=today
        ).count()
        
        is_clocked_in = clock_ins > clock_outs

    # Handle POST request for action submission
    if request.method == 'POST':
        action = request.POST.get('action')
        
        if action:
            # Clock in is always allowed
            if action == 'clock_in':
                AttendanceLog.objects.create(user=request.user, action=action)
                messages.success(request, "Successfully clocked in!")
            
            # All other actions require the user to be clocked in
            elif action == 'clock_out':
                AttendanceLog.objects.create(user=request.user, action=action)
                messages.success(request, "Successfully clocked out!")
            
            # For break/lunch actions, check if user is clocked in
            elif is_clocked_in:
                if action == 'break_start' and time_allocation.break_minutes_remaining() > 0:
                    if ongoing_break:
                        messages.error(request, "You already have an ongoing break!")
                    elif ongoing_lunch:
                        messages.error(request, "You can't start a break while on lunch!")
                    else:
                        time_allocation.break_start_time = timezone.now()
                        time_allocation.save()
                        AttendanceLog.objects.create(user=request.user, action=action)
                        messages.success(request, f"Break started! You have {time_allocation.break_minutes_remaining()} minutes remaining.")
                
                elif action == 'break_end':
                    if ongoing_break:
                        # Calculate minutes used in this break
                        break_duration = timezone.now() - time_allocation.break_start_time
                        minutes_used = round(break_duration.total_seconds() / 60)
                        
                        # Update time allocation
                        time_allocation.break_minutes_used += minutes_used
                        time_allocation.break_start_time = None
                        time_allocation.save()
                        
                        AttendanceLog.objects.create(user=request.user, action=action)
                        
                        # Check if break time was exceeded
                        if time_allocation.is_break_exceeded():
                            messages.warning(request, 
                                f"Break ended! You've used {minutes_used} minutes. " +
                                f"You've exceeded your daily break allowance by {time_allocation.break_minutes_exceeded()} minutes."
                            )
                        else:
                            messages.success(request, 
                                f"Break ended! You've used {minutes_used} minutes. " +
                                f"{time_allocation.break_minutes_remaining()} minutes remaining today."
                            )
                    else:
                        messages.error(request, "You don't have an active break to end!")
                
                elif action == 'lunch_start' and time_allocation.lunch_minutes_remaining() > 0:
                    if ongoing_lunch:
                        messages.error(request, "You already have an ongoing lunch!")
                    elif ongoing_break:
                        messages.error(request, "You can't start lunch while on break!")
                    else:
                        time_allocation.lunch_start_time = timezone.now()
                        time_allocation.save()
                        AttendanceLog.objects.create(user=request.user, action=action)
                        messages.success(request, f"Lunch started! You have {time_allocation.lunch_minutes_remaining()} minutes remaining.")
                
                elif action == 'lunch_end':
                    if ongoing_lunch:
                        # Calculate minutes used in this lunch
                        lunch_duration = timezone.now() - time_allocation.lunch_start_time
                        minutes_used = round(lunch_duration.total_seconds() / 60)
                        
                        # Update time allocation
                        time_allocation.lunch_minutes_used += minutes_used
                        time_allocation.lunch_start_time = None
                        time_allocation.save()
                        
                        AttendanceLog.objects.create(user=request.user, action=action)
                        
                        # Check if lunch time was exceeded
                        if time_allocation.is_lunch_exceeded():
                            messages.warning(request, 
                                f"Lunch ended! You've used {minutes_used} minutes. " +
                                f"You've exceeded your daily lunch allowance by {time_allocation.lunch_minutes_exceeded()} minutes."
                            )
                        else:
                            messages.success(request, 
                                f"Lunch ended! You've used {minutes_used} minutes. " +
                                f"{time_allocation.lunch_minutes_remaining()} minutes remaining today."
                            )
                    else:
                        messages.error(request, "You don't have an active lunch to end!")
                
                elif action == 'combined_break_start':
                    if ongoing_break or ongoing_lunch:
                        messages.error(request, "You already have an ongoing break or lunch!")
                    else:
                        # Start using break time first
                        time_allocation.break_start_time = timezone.now()
                        time_allocation.save()
                        AttendanceLog.objects.create(user=request.user, action=action)
                        combined_minutes = time_allocation.break_minutes_remaining() + time_allocation.lunch_minutes_remaining()
                        messages.success(request, f"Combined break started! You have {combined_minutes} minutes available (Break: {time_allocation.break_minutes_remaining()} min, Lunch: {time_allocation.lunch_minutes_remaining()} min).")
                
                elif action == 'combined_break_end':
                    if ongoing_break:
                        # Calculate minutes used in this break
                        break_duration = timezone.now() - time_allocation.break_start_time
                        minutes_used = round(break_duration.total_seconds() / 60)
                        
                        # First use break time
                        available_break_minutes = time_allocation.break_minutes_remaining()
                        if minutes_used <= available_break_minutes:
                            # All time comes from break
                            time_allocation.break_minutes_used += minutes_used
                            time_used_message = f"Break time used: {minutes_used} min"
                            remaining_message = f"Break time remaining: {time_allocation.break_minutes_remaining()} min, Lunch: {time_allocation.lunch_minutes_remaining()} min"
                        else:
                            # Used all break time plus some lunch time
                            lunch_minutes_used = minutes_used - available_break_minutes
                            
                            # Use all remaining break time
                            time_allocation.break_minutes_used += available_break_minutes
                            
                            # Then use lunch time for the remainder
                            time_allocation.lunch_minutes_used += lunch_minutes_used
                            
                            time_used_message = f"Break time used: {available_break_minutes} min, Lunch time used: {lunch_minutes_used} min"
                            
                            if time_allocation.lunch_minutes_remaining() > 0:
                                remaining_message = f"Break time: 0 min, Lunch time remaining: {time_allocation.lunch_minutes_remaining()} min"
                            else:
                                exceeded = time_allocation.lunch_minutes_exceeded()
                                remaining_message = f"You've exceeded your combined break/lunch allowance by {exceeded} minutes."
                        
                        # Reset the break start time
                        time_allocation.break_start_time = None
                        time_allocation.save()
                        
                        # Create the log
                        AttendanceLog.objects.create(user=request.user, action=action)
                        
                        # Success message
                        if time_allocation.is_break_exceeded() or time_allocation.is_lunch_exceeded():
                            messages.warning(request, f"Combined break ended! {time_used_message}. {remaining_message}")
                        else:
                            messages.success(request, f"Combined break ended! {time_used_message}. {remaining_message}")
                    
                    elif ongoing_lunch:
                        messages.error(request, "You have an ongoing lunch, not a combined break. Please end your lunch instead.")
                    else:
                        messages.error(request, "You don't have an active combined break to end!")
            
            # Show error if user tries to take a break without clocking in
            else:
                messages.error(request, "You must clock in before you can perform this action!")
        
        # Redirect to maintain POST-redirect-GET pattern
        return redirect('tracker')
    
    # Prepare context with all necessary data
    context = {
        'logs': logs,
        'page_obj': page_obj,  # Add this
        'today': date.today(),
        'selected_date': selected_date,
        'is_today': is_today,
        'break_minutes_remaining': time_allocation.break_minutes_remaining(),
        'lunch_minutes_remaining': time_allocation.lunch_minutes_remaining(),
        'break_minutes_exceeded': time_allocation.break_minutes_exceeded(),
        'lunch_minutes_exceeded': time_allocation.lunch_minutes_exceeded(),
        'is_break_exceeded': time_allocation.is_break_exceeded(),
        'is_lunch_exceeded': time_allocation.is_lunch_exceeded(),
        'ongoing_break': ongoing_break,
        'ongoing_lunch': ongoing_lunch,
        'current_status': current_status,
        'break_minutes_used': time_allocation.break_minutes_used,
        'lunch_minutes_used': time_allocation.lunch_minutes_used,
        'page': page,  # Add this to preserve page number during requests
        'is_clocked_in': is_clocked_in,
    }
    
    # Add timestamps for ongoing activities
    if ongoing_break:
        break_start = time_allocation.break_start_time
        break_elapsed = timezone.now() - break_start
        break_elapsed_minutes = round(break_elapsed.total_seconds() / 60)
        context['break_elapsed_minutes'] = break_elapsed_minutes
        context['break_start_time'] = break_start
    
    if ongoing_lunch:
        lunch_start = time_allocation.lunch_start_time
        lunch_elapsed = timezone.now() - lunch_start
        lunch_elapsed_minutes = round(lunch_elapsed.total_seconds() / 60)
        context['lunch_elapsed_minutes'] = lunch_elapsed_minutes
        context['lunch_start_time'] = lunch_start
    
    return render(request, 'tracker.html', context)

# 📊 USER DASHBOARD
@login_required
def dashboard(request):
    logs = AttendanceLog.objects.filter(user=request.user).order_by('-timestamp')
    return render(request, 'dashboard.html', {'logs': logs})

# 📊 ADMIN DASHBOARD
@user_passes_test(lambda u: u.is_superuser)
def admin_dashboard(request):
    logs = AttendanceLog.objects.select_related('user').order_by('-timestamp')
    return render(request, 'admin_dashboard.html', {'logs': logs})

# 📤 EXPORT USER CSV
@login_required
def export_csv(request):
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="{request.user.username}_attendance.csv"'

    writer = csv.writer(response)
    writer.writerow([
        'Date', 'Action', 'Time', 
        'Break Minutes Remaining', 'Break Minutes Exceeded',
        'Lunch Minutes Remaining', 'Lunch Minutes Exceeded', 
        'Note'
    ])

    # Get all unique dates with logs
    logs = AttendanceLog.objects.filter(user=request.user).order_by('timestamp')
    
    # Group logs by date to track remaining time
    dates = set(log.timestamp.date() for log in logs)
    
    for log_date in sorted(dates):
        # Get the time allocation for this date
        time_allocation = DailyTimeAllocation.objects.filter(
            user=request.user,
            date=log_date
        ).first()
        
        # If no time allocation exists for this date, create default values
        break_remaining = 30
        lunch_remaining = 60
        break_exceeded = 0
        lunch_exceeded = 0
        
        if time_allocation:
            break_remaining = time_allocation.break_minutes_remaining()
            lunch_remaining = time_allocation.lunch_minutes_remaining()
            break_exceeded = time_allocation.break_minutes_exceeded()
            lunch_exceeded = time_allocation.lunch_minutes_exceeded()
            
        # Get logs for this specific date
        daily_logs = logs.filter(timestamp__date=log_date)
        
        for log in daily_logs:
            # Format the time in local timezone with 12-hour AM/PM format
            local_time = timezone.localtime(log.timestamp).strftime('%I:%M %p')
            
            # For break_end and lunch_end, calculate the updated remaining time
            current_break_remaining = break_remaining
            current_lunch_remaining = lunch_remaining
            current_break_exceeded = break_exceeded
            current_lunch_exceeded = lunch_exceeded
            
            if log.action == 'break_end' and time_allocation:
                # Find the most recent break_start before this end
                break_starts = logs.filter(
                    action='break_start',
                    timestamp__lt=log.timestamp,
                    timestamp__date=log_date
                ).order_by('-timestamp')
                
                if break_starts.exists():
                    break_start = break_starts.first()
                    break_duration = log.timestamp - break_start.timestamp
                    minutes_used = round(break_duration.total_seconds() / 60)
                    # Calculate the specific break metrics for this log
                    total_used_so_far = minutes_used
                    prev_break_ends = logs.filter(
                        action='break_end',
                        timestamp__lt=log.timestamp,
                        timestamp__date=log_date
                    )
                    for prev_end in prev_break_ends:
                        # Find corresponding start for each previous end
                        prev_start = logs.filter(
                            action='break_start',
                            timestamp__lt=prev_end.timestamp,
                            timestamp__date=log_date
                        ).order_by('-timestamp').first()
                        
                        if prev_start:
                            prev_duration = prev_end.timestamp - prev_start.timestamp
                            prev_minutes = round(prev_duration.total_seconds() / 60)
                            total_used_so_far += prev_minutes
                    
                    if total_used_so_far > 30:
                        current_break_remaining = 0
                        current_break_exceeded = total_used_so_far - 30
                    else:
                        current_break_remaining = 30 - total_used_so_far
                        current_break_exceeded = 0
                    
            elif log.action == 'lunch_end' and time_allocation:
                # Find the most recent lunch_start before this end
                lunch_starts = logs.filter(
                    action='lunch_start',
                    timestamp__lt=log.timestamp,
                    timestamp__date=log_date
                ).order_by('-timestamp')
                
                if lunch_starts.exists():
                    lunch_start = lunch_starts.first()
                    lunch_duration = log.timestamp - lunch_start.timestamp
                    minutes_used = round(lunch_duration.total_seconds() / 60)
                    # Calculate the specific lunch metrics for this log
                    total_used_so_far = minutes_used
                    prev_lunch_ends = logs.filter(
                        action='lunch_end',
                        timestamp__lt=log.timestamp,
                        timestamp__date=log_date
                    )
                    for prev_end in prev_lunch_ends:
                        # Find corresponding start for each previous end
                        prev_start = logs.filter(
                            action='lunch_start',
                            timestamp__lt=prev_end.timestamp,
                            timestamp__date=log_date
                        ).order_by('-timestamp').first()
                        
                        if prev_start:
                            prev_duration = prev_end.timestamp - prev_start.timestamp
                            prev_minutes = round(prev_duration.total_seconds() / 60)
                            total_used_so_far += prev_minutes
                    
                    if total_used_so_far > 60:
                        current_lunch_remaining = 0
                        current_lunch_exceeded = total_used_so_far - 60
                    else:
                        current_lunch_remaining = 60 - total_used_so_far
                        current_lunch_exceeded = 0
            
            action_display = log.action
            if log.action == 'combined_break_start':
                action_display = 'Combined Break Start'
            elif log.action == 'combined_break_end':
                action_display = 'Combined Break End'

            writer.writerow([
                log_date, 
                action_display, 
                local_time,
                current_break_remaining if log.action in ['break_start', 'break_end', 'combined_break_start', 'combined_break_end'] else '',
                current_break_exceeded if (log.action == 'break_end' or log.action == 'combined_break_end') and current_break_exceeded > 0 else '',
                current_lunch_remaining if log.action in ['lunch_start', 'lunch_end', 'combined_break_start', 'combined_break_end'] else '',
                current_lunch_exceeded if (log.action == 'lunch_end' or log.action == 'combined_break_end') and current_lunch_exceeded > 0 else '',
                log.note or ''
            ])
    
    return response

# 📤 EXPORT ADMIN CSV (ALL USERS)
@user_passes_test(lambda u: u.is_superuser)
def admin_export_csv(request):
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="all_attendance_logs.csv"'

    writer = csv.writer(response)
    writer.writerow([
        'Username', 'Date', 'Action', 'Time', 
        'Break Minutes Remaining', 'Break Minutes Exceeded',
        'Lunch Minutes Remaining', 'Lunch Minutes Exceeded', 
        'Note'
    ])
    
    # Similar implementation to the user export_csv but for all users
    # [Implementation similar to above - add exceeded time columns]
