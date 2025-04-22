from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required, user_passes_test
from django.http import HttpResponse
from .models import AttendanceLog, DailyTimeAllocation
from django.utils import timezone
import csv
from datetime import date, datetime, timedelta, time
from django.db.models import Q
from django.contrib import messages
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
from django.contrib.auth.models import User
from collections import defaultdict
from django.utils.timezone import localtime
import pytz
from django.core.management.base import BaseCommand
from django.conf import settings

# 👤 LOGIN
def login_view(request):
    if request.user.is_authenticated:
        if request.user.is_superuser:
            return redirect('admin_dashboard')  # Redirect superusers to admin dashboard
        else:
            return redirect('tracker')  # Redirect regular users to tracker

    if request.method == 'POST':
        username = request.POST['username']
        password = request.POST['password']
        
        user = authenticate(request, username=username, password=password)
        if user:
            login(request, user)
            if user.is_superuser:
                return redirect('admin_dashboard')  # Redirect superusers to admin dashboard
            else:
                return redirect('tracker')  # Redirect regular users to tracker
        else:
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
            is_today = (today == date.today())
        except ValueError:
            selected_date = today = date.today()
            is_today = True
    else:
        selected_date = today = date.today()
        is_today = True
    
    # Get or create daily time allocation for today
    time_allocation, created = DailyTimeAllocation.objects.get_or_create(
        user=request.user,
        date=today,
    )
    
    # Define shift hours (10 PM to 7 AM next day)
    now = localtime(timezone.now())
    current_date = now.date()
    
    # Create datetime objects for start and end of shift
    # If current time is before 7 AM, shift started yesterday at 10 PM
    if now.time() < time(7, 0):
        shift_start_date = current_date - timedelta(days=1)
        shift_end_date = current_date
    else:
        shift_start_date = current_date
        shift_end_date = current_date + timedelta(days=1)
        
    shift_start = datetime.combine(shift_start_date, time(22, 0)).replace(tzinfo=pytz.timezone('Asia/Manila'))
    shift_end = datetime.combine(shift_end_date, time(7, 0)).replace(tzinfo=pytz.timezone('Asia/Manila'))
    
    # Check for late clock in
    clocked_in_late = False
    minutes_late = 0
    first_clock_in = None
    
    if is_today:
        # Get the first clock in for today or yesterday's shift
        if now.time() < time(7, 0):
            # We're before 7 AM, so check yesterday's clock-ins
            first_clock_in = AttendanceLog.objects.filter(
                user=request.user,
                action='clock_in',
                timestamp__date__gte=shift_start_date,
                timestamp__date__lte=shift_end_date
            ).order_by('timestamp').first()
        else:
            # We're after 7 AM, so check today's clock-ins
            first_clock_in = AttendanceLog.objects.filter(
                user=request.user,
                action='clock_in',
                timestamp__date=shift_start_date
            ).order_by('timestamp').first()
        
        if first_clock_in:
            # If clocked in after 10 PM
            if first_clock_in.timestamp > shift_start:
                clocked_in_late = True
                time_diff = first_clock_in.timestamp - shift_start
                minutes_late = int(time_diff.total_seconds() // 60)
    
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
    ongoing_break1 = False
    ongoing_break2 = False
    ongoing_lunch = False
    
    # Check if there's an ongoing break or lunch
    if time_allocation.break1_start_time:
        ongoing_break1 = True
        current_status = 'on_break1'
    
    if time_allocation.break2_start_time:
        ongoing_break2 = True
        current_status = 'on_break2'

    if time_allocation.lunch_start_time:
        ongoing_lunch = True
        current_status = 'at_lunch'

    # Check if user is clocked in across days
    # First, check if the user is clocked in today
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
    
    # If viewing today, we also need to check if they're still clocked in from yesterday
    if is_today:
        # If not clocked in today, check if they clocked in yesterday but didn't clock out
        yesterday = date.today() - timedelta(days=1)
        if not clocked_in_today and not clocked_out_today:
            yesterday_clock_in = AttendanceLog.objects.filter(
                user=request.user,
                action='clock_in',
                timestamp__date=yesterday
            ).order_by('-timestamp').first()
            
            yesterday_clock_out = AttendanceLog.objects.filter(
                user=request.user,
                action='clock_out',
                timestamp__date=yesterday
            ).order_by('-timestamp').first()
            
            # If they clocked in yesterday and either didn't clock out or clocked in after clocking out
            if yesterday_clock_in and (not yesterday_clock_out or yesterday_clock_in.timestamp > yesterday_clock_out.timestamp):
                # They're still clocked in from yesterday
                is_clocked_in = True
                # Create a note that they're still clocked in from yesterday
                messages.info(request, f"You are still clocked in from yesterday ({yesterday.strftime('%Y-%m-%d')})")
    
    # If the user has clocked in but not clocked out, they are considered clocked in
    if clocked_in_today and not clocked_out_today:
        is_clocked_in = True
        if not (ongoing_break1 or ongoing_break2 or ongoing_lunch):
            current_status = 'working'
    
    # Only allow clock actions on the current day, not in history
    can_perform_clock_actions = is_today

    # Handle POST request for action submission
    if request.method == 'POST':
        action = request.POST.get('action')
        
        # CLOCK IN
        if action == 'clock_in':
            now = timezone.now()
            
            # Calculate if late
            is_late = False
            late_minutes = 0
            
            # Check if clock-in is after shift start time (10 PM)
            shift_start_time = time(22, 0)
            current_time = now.time()
            
            # If it's between midnight and 7 AM, the shift started at 10 PM yesterday
            if current_time < time(7, 0):
                shift_date = now.date() - timedelta(days=1)
            else:
                shift_date = now.date()
            
            shift_start_datetime = datetime.combine(shift_date, shift_start_time)
            shift_start_datetime = shift_start_datetime.replace(tzinfo=pytz.timezone('Asia/Manila'))
            
            # Check if we need to reset break/lunch allocations for a new shift
            # A new shift starts after 7 AM, so if we're past 7 AM, we should reset for today
            if current_time >= time(7, 0):
                # Get today's allocation and reset it if it exists
                today_allocation = DailyTimeAllocation.objects.filter(
                    user=request.user,
                    date=now.date()
                ).first()
                
                if today_allocation:
                    today_allocation.break1_minutes_used = 0
                    today_allocation.break2_minutes_used = 0
                    today_allocation.lunch_minutes_used = 0
                    today_allocation.break1_start_time = None
                    today_allocation.break2_start_time = None
                    today_allocation.lunch_start_time = None
                    today_allocation.save()
                    messages.info(request, 'Break and lunch allocations have been reset for today\'s shift.')
            
            # Only check for lateness if clocking in after the shift start time
            if now > shift_start_datetime:
                is_late = True
                time_diff = now - shift_start_datetime
                late_minutes = int(time_diff.total_seconds() // 60)
            
            # Create the log with late information if applicable
            note = None
            if is_late:
                note = f"Late arrival: {late_minutes} minutes"
            else:
                # Add a note for early clock-ins if you want to track them
                time_diff = shift_start_datetime - now
                early_minutes = int(time_diff.total_seconds() // 60)
                if early_minutes > 0:
                    note = f"Early arrival: {early_minutes} minutes before shift"
            
            log = AttendanceLog(user=request.user, action='clock_in', note=note)
            log.save()
            
            if is_late:
                messages.warning(request, f'Clocked in {late_minutes} minutes late!')
            else:
                messages.success(request, 'Clocked in successfully!')
                
            return redirect('tracker')
        
        # CLOCK OUT
        elif action == 'clock_out':
            log = AttendanceLog(user=request.user, action='clock_out')
            log.save()
            messages.success(request, 'Clocked out successfully!')
            return redirect('tracker')
        
        # START BREAK 1
        elif action == 'start_break1':
            if not is_clocked_in:
                messages.error(request, 'You must be clocked in to take a break.')
            elif ongoing_break1 or ongoing_break2 or ongoing_lunch:
                messages.error(request, 'You are already on a break or lunch.')
            elif time_allocation.is_break1_exceeded():
                messages.error(request, 'You have already used all your break 1 time (15 minutes).')
            else:
                time_allocation.break1_start_time = timezone.now()
                time_allocation.save()
                log = AttendanceLog(user=request.user, action='start_break1')
                log.save()
                messages.success(request, 'Break 1 started!')
            return redirect('tracker')
        
        # START BREAK 2
        elif action == 'start_break2':
            if not is_clocked_in:
                messages.error(request, 'You must be clocked in to take a break.')
            elif ongoing_break1 or ongoing_break2 or ongoing_lunch:
                messages.error(request, 'You are already on a break or lunch.')
            elif time_allocation.is_break2_exceeded():
                messages.error(request, 'You have already used all your break 2 time (15 minutes).')
            else:
                time_allocation.break2_start_time = timezone.now()
                time_allocation.save()
                log = AttendanceLog(user=request.user, action='start_break2')
                log.save()
                messages.success(request, 'Break 2 started!')
            return redirect('tracker')
        
        # END BREAK 1
        elif action == 'end_break1':
            if not ongoing_break1:
                messages.error(request, 'No break 1 in progress.')
            else:
                # Calculate minutes used
                break_duration = timezone.now() - time_allocation.break1_start_time
                minutes_used = break_duration.total_seconds() // 60
                
                # Update time allocation
                time_allocation.break1_minutes_used += int(minutes_used)
                time_allocation.break1_start_time = None
                time_allocation.save()
                
                log = AttendanceLog(
                    user=request.user, 
                    action='end_break1',
                    note=f'Break 1 duration: {int(minutes_used)} minutes'
                )
                log.save()
                messages.success(request, f'Break 1 ended. You used {int(minutes_used)} minutes.')
            return redirect('tracker')
        
        # END BREAK 2
        elif action == 'end_break2':
            if not ongoing_break2:
                messages.error(request, 'No break 2 in progress.')
            else:
                # Calculate minutes used
                break_duration = timezone.now() - time_allocation.break2_start_time
                minutes_used = break_duration.total_seconds() // 60
                
                # Update time allocation
                time_allocation.break2_minutes_used += int(minutes_used)
                time_allocation.break2_start_time = None
                time_allocation.save()
                
                log = AttendanceLog(
                    user=request.user, 
                    action='end_break2',
                    note=f'Break 2 duration: {int(minutes_used)} minutes'
                )
                log.save()
                messages.success(request, f'Break 2 ended. You used {int(minutes_used)} minutes.')
            return redirect('tracker')
        
        # START LUNCH
        elif action == 'start_lunch':
            if not is_clocked_in:
                messages.error(request, 'You must be clocked in to take lunch.')
            elif ongoing_break1 or ongoing_break2 or ongoing_lunch:
                messages.error(request, 'You are already on a break or lunch.')
            elif time_allocation.is_lunch_exceeded():
                messages.error(request, 'You have already used all your lunch time (60 minutes).')
            else:
                time_allocation.lunch_start_time = timezone.now()
                time_allocation.save()
                log = AttendanceLog(user=request.user, action='start_lunch')
                log.save()
                messages.success(request, 'Lunch started!')
            return redirect('tracker')
        
        # END LUNCH
        elif action == 'end_lunch':
            if not ongoing_lunch:
                messages.error(request, 'No lunch in progress.')
            else:
                # Calculate minutes used
                lunch_duration = timezone.now() - time_allocation.lunch_start_time
                minutes_used = lunch_duration.total_seconds() // 60
                
                # Update time allocation
                time_allocation.lunch_minutes_used += int(minutes_used)
                time_allocation.lunch_start_time = None
                time_allocation.save()
                
                log = AttendanceLog(
                    user=request.user, 
                    action='end_lunch',
                    note=f'Lunch duration: {int(minutes_used)} minutes'
                )
                log.save()
                messages.success(request, f'Lunch ended. You used {int(minutes_used)} minutes.')
            return redirect('tracker')
    
    # Calculate percentages for progress bars
    break1_percentage = min(time_allocation.break1_minutes_used / 15 * 100, 100) if time_allocation.break1_minutes_used > 0 else 0
    break2_percentage = min(time_allocation.break2_minutes_used / 15 * 100, 100) if time_allocation.break2_minutes_used > 0 else 0
    lunch_percentage = min(time_allocation.lunch_minutes_used / 60 * 100, 100) if time_allocation.lunch_minutes_used > 0 else 0
    
    # Prepare context with all necessary data
    context = {
        'logs': logs,
        'page_obj': page_obj,
        'today': date.today(),
        'selected_date': selected_date,
        'is_today': is_today,
        'break1_minutes_remaining': time_allocation.break1_minutes_remaining(),
        'break2_minutes_remaining': time_allocation.break2_minutes_remaining(),
        'lunch_minutes_remaining': time_allocation.lunch_minutes_remaining(),
        'break1_minutes_exceeded': time_allocation.break1_minutes_exceeded(),
        'break2_minutes_exceeded': time_allocation.break2_minutes_exceeded(),
        'lunch_minutes_exceeded': time_allocation.lunch_minutes_exceeded(),
        'is_break1_exceeded': time_allocation.is_break1_exceeded(),
        'is_break2_exceeded': time_allocation.is_break2_exceeded(),
        'is_lunch_exceeded': time_allocation.is_lunch_exceeded(),
        'ongoing_break1': ongoing_break1,
        'ongoing_break2': ongoing_break2,
        'ongoing_lunch': ongoing_lunch,
        'current_status': current_status,
        'break1_minutes_used': time_allocation.break1_minutes_used,
        'break2_minutes_used': time_allocation.break2_minutes_used,
        'lunch_minutes_used': time_allocation.lunch_minutes_used,
        'break1_percentage': break1_percentage,
        'break2_percentage': break2_percentage,
        'lunch_percentage': lunch_percentage,
        'is_clocked_in': is_clocked_in,
        'can_perform_clock_actions': can_perform_clock_actions,
        'clocked_in_late': clocked_in_late,
        'minutes_late': minutes_late,
        'shift_start_time': '10:00 PM',
        'shift_end_time': '7:00 AM',
    }
    
    # Add timestamps for ongoing activities
    if ongoing_break1:
        context['break1_start_time'] = time_allocation.break1_start_time
    
    if ongoing_break2:
        context['break2_start_time'] = time_allocation.break2_start_time
    
    if ongoing_lunch:
        context['lunch_start_time'] = time_allocation.lunch_start_time
    
    return render(request, 'tracker.html', context)

# 📊 USER DASHBOARD
@login_required
def dashboard(request):
    logs = AttendanceLog.objects.filter(user=request.user).order_by('-timestamp')
    return render(request, 'dashboard.html', {'logs': logs})

# 📊 ADMIN DASHBOARD
@user_passes_test(lambda u: u.is_superuser)
def admin_dashboard(request):
    # Get filter parameters
    selected_user = request.GET.get('user')
    selected_date = request.GET.get('date')
    
    # Get page size from request (default to 5 if not specified)
    page_size = request.GET.get('size', '5')
    try:
        page_size = int(page_size)
        # Limit page size options to valid choices
        if page_size not in [5, 10, 25, 50]:
            page_size = 5
    except ValueError:
        page_size = 5
    
    # Base query for logs
    logs_query = AttendanceLog.objects.select_related('user')
    
    # Apply filters
    if selected_user:
        logs_query = logs_query.filter(user_id=selected_user)
        selected_user_obj = User.objects.get(id=selected_user)
    else:
        selected_user_obj = None
        
    if selected_date:
        try:
            filter_date = datetime.strptime(selected_date, '%Y-%m-%d').date()
            logs_query = logs_query.filter(timestamp__date=filter_date)
        except ValueError:
            pass
    
    # Order logs
    logs = logs_query.order_by('-timestamp')
    
    # Pagination with the selected page size
    page = request.GET.get('page', 1)
    paginator = Paginator(logs, page_size)
    
    try:
        page_obj = paginator.page(page)
    except PageNotAnInteger:
        page_obj = paginator.page(1)
    except EmptyPage:
        page_obj = paginator.page(paginator.num_pages)
    
    # Get all users (for filter dropdown and sidebar)
    users = User.objects.all().order_by('username')
    
    # Calculate current stats
    today = date.today()
    total_users = User.objects.count()
    
    # Track user statuses for sidebar
    clocked_in_users = set()
    break_users = set()
    lunch_users = set()
    
    # Find users currently clocked in, on break, or lunch
    for user_obj in users:
        # Check if user is clocked in
        clock_ins_today = AttendanceLog.objects.filter(
            user=user_obj,
            action='clock_in',
            timestamp__date=today
        ).count()
        
        clock_outs_today = AttendanceLog.objects.filter(
            user=user_obj,
            action='clock_out',
            timestamp__date=today
        ).count()
        
        if clock_ins_today > clock_outs_today:
            clocked_in_users.add(user_obj.id)
            
            # Check if they're on break or lunch
            try:
                time_allocation = DailyTimeAllocation.objects.get(user=user_obj, date=today)
                if time_allocation.break1_start_time or time_allocation.break2_start_time:
                    break_users.add(user_obj.id)
                if time_allocation.lunch_start_time:
                    lunch_users.add(user_obj.id)
            except DailyTimeAllocation.DoesNotExist:
                pass
    
    # Get time allocations for all users for their log dates
    # Create a nested dictionary: {user_id: {date: allocation}}
    user_time_allocations = defaultdict(dict)
    
    # Get unique user and date combinations from the logs
    user_date_pairs = logs.values('user_id', 'timestamp__date').distinct()
    
    for pair in user_date_pairs:
        user_id = pair['user_id']
        log_date = pair['timestamp__date']
        
        # Get or create time allocation for this user and date
        time_allocation, _ = DailyTimeAllocation.objects.get_or_create(
            user_id=user_id,
            date=log_date
        )
        
        # Store in our dictionary
        user_time_allocations[user_id][log_date] = time_allocation
    
    context = {
        'logs': page_obj,
        'page_obj': page_obj,
        'users': users,
        'selected_user': selected_user,
        'selected_user_obj': selected_user_obj,
        'selected_date': selected_date,
        'total_users': total_users,
        'users_clocked_in': len(clocked_in_users),
        'users_on_break': len(break_users),
        'users_on_lunch': len(lunch_users),
        'user_time_allocations': dict(user_time_allocations),
        'clocked_in_users': clocked_in_users,
        'break_users': break_users,
        'lunch_users': lunch_users,
    }
    
    return render(request, 'admin_dashboard.html', context)

# 📤 EXPORT USER CSV
@login_required
def export_csv(request):
    # Import the necessary libraries
    import openpyxl
    from openpyxl.styles import Font, Alignment, PatternFill
    from io import BytesIO
    
    # Create a new workbook and sheets
    wb = openpyxl.Workbook()
    
    # Create the sheets we need with valid names
    clock_sheet = wb.active
    clock_sheet.title = "Clock In-Out"  # Changed from "Clock In/Out" to "Clock In-Out"
    break_sheet = wb.create_sheet(title="Breaks")
    lunch_sheet = wb.create_sheet(title="Lunch")
    
    # Set up headers with formatting
    headers = {
        "Clock In-Out": ["Date", "Action", "Time", "Note", "Late (minutes)"],  # Updated key name
        "Breaks": ["Date", "Action", "Time", "Break Type", "Duration (minutes)", "Remaining Time", "Exceeded Time", "Note"],
        "Lunch": ["Date", "Action", "Time", "Duration (minutes)", "Remaining Time", "Exceeded Time", "Note"]
    }
    
    header_font = Font(bold=True)
    header_fill = PatternFill(start_color="DDDDDD", end_color="DDDDDD", fill_type="solid")
    
    # Set up headers for each sheet
    for col_num, header in enumerate(headers["Clock In-Out"], 1):
        cell = clock_sheet.cell(row=1, column=col_num)
        cell.value = header
        cell.font = header_font
        cell.fill = header_fill
    
    for col_num, header in enumerate(headers["Breaks"], 1):
        cell = break_sheet.cell(row=1, column=col_num)
        cell.value = header
        cell.font = header_font
        cell.fill = header_fill
    
    for col_num, header in enumerate(headers["Lunch"], 1):
        cell = lunch_sheet.cell(row=1, column=col_num)
        cell.value = header
        cell.font = header_font
        cell.fill = header_fill
    
    # Get all logs for the user
    logs = AttendanceLog.objects.filter(user=request.user).order_by('timestamp')
    
    # Track row numbers for each sheet
    clock_row = 2
    break_row = 2
    lunch_row = 2
    
    # Group logs by date to track remaining time
    dates = set(log.timestamp.date() for log in logs)
    
    for log_date in sorted(dates):
        # Get the time allocation for this date
        time_allocation = DailyTimeAllocation.objects.filter(
            user=request.user,
            date=log_date
        ).first()
        
        # If no time allocation exists for this date, create default values
        break1_remaining = 15
        break2_remaining = 15
        lunch_remaining = 60
        break1_exceeded = 0
        break2_exceeded = 0
        lunch_exceeded = 0
        
        if time_allocation:
            break1_remaining = time_allocation.break1_minutes_remaining()
            break2_remaining = time_allocation.break2_minutes_remaining()
            lunch_remaining = time_allocation.lunch_minutes_remaining()
            break1_exceeded = time_allocation.break1_minutes_exceeded()
            break2_exceeded = time_allocation.break2_minutes_exceeded()
            lunch_exceeded = time_allocation.lunch_minutes_exceeded()
            
        # Get logs for this specific date
        daily_logs = logs.filter(timestamp__date=log_date)
        
        for log in daily_logs:
            # Format the time in local timezone with 12-hour AM/PM format
            local_time = timezone.localtime(log.timestamp).strftime('%I:%M %p')
            
            # Process based on action type
            if log.action in ['clock_in', 'clock_out']:
                # Add to Clock In-Out sheet
                clock_sheet.cell(row=clock_row, column=1).value = log_date.strftime('%Y-%m-%d')
                clock_sheet.cell(row=clock_row, column=2).value = log.action.replace('_', ' ').title()
                clock_sheet.cell(row=clock_row, column=3).value = local_time
                clock_sheet.cell(row=clock_row, column=4).value = log.note or ''
                
                # Extract late minutes from note if it's a clock_in
                if log.action == 'clock_in' and log.note and 'Late arrival:' in log.note:
                    try:
                        minutes_late = int(log.note.split('Late arrival:')[1].split('minutes')[0].strip())
                        clock_sheet.cell(row=clock_row, column=5).value = minutes_late
                    except (ValueError, IndexError):
                        pass
                    
                clock_row += 1
                
            elif log.action in ['start_break1', 'end_break1', 'start_break2', 'end_break2']:
                # Add to Breaks sheet
                break_sheet.cell(row=break_row, column=1).value = log_date.strftime('%Y-%m-%d')
                break_sheet.cell(row=break_row, column=2).value = 'Start' if 'start' in log.action else 'End'
                break_sheet.cell(row=break_row, column=3).value = local_time
                break_sheet.cell(row=break_row, column=4).value = 'Break 1' if 'break1' in log.action else 'Break 2'
                
                # Add duration if it's an end break action
                if 'end' in log.action and log.note and 'duration:' in log.note.lower():
                    try:
                        duration = int(log.note.split('duration:')[1].split('minutes')[0].strip())
                        break_sheet.cell(row=break_row, column=5).value = duration
                    except (ValueError, IndexError):
                        pass
                
                # Add remaining time
                if 'break1' in log.action:
                    break_sheet.cell(row=break_row, column=6).value = break1_remaining
                    if 'end' in log.action and break1_exceeded > 0:
                        break_sheet.cell(row=break_row, column=7).value = break1_exceeded
                else:  # break2
                    break_sheet.cell(row=break_row, column=6).value = break2_remaining
                    if 'end' in log.action and break2_exceeded > 0:
                        break_sheet.cell(row=break_row, column=7).value = break2_exceeded
                
                break_sheet.cell(row=break_row, column=8).value = log.note or ''
                break_row += 1
                
            elif log.action in ['start_lunch', 'end_lunch']:
                # Add to Lunch sheet
                lunch_sheet.cell(row=lunch_row, column=1).value = log_date.strftime('%Y-%m-%d')
                lunch_sheet.cell(row=lunch_row, column=2).value = 'Start' if 'start' in log.action else 'End'
                lunch_sheet.cell(row=lunch_row, column=3).value = local_time
                
                # Add duration if it's an end lunch action
                if log.action == 'end_lunch' and log.note and 'duration:' in log.note.lower():
                    try:
                        duration = int(log.note.split('duration:')[1].split('minutes')[0].strip())
                        lunch_sheet.cell(row=lunch_row, column=4).value = duration
                    except (ValueError, IndexError):
                        pass
                
                lunch_sheet.cell(row=lunch_row, column=5).value = lunch_remaining
                if log.action == 'end_lunch' and lunch_exceeded > 0:
                    lunch_sheet.cell(row=lunch_row, column=6).value = lunch_exceeded
                
                lunch_sheet.cell(row=lunch_row, column=7).value = log.note or ''
                lunch_row += 1
    
    # Auto-adjust column widths
    for sheet in [clock_sheet, break_sheet, lunch_sheet]:
        for column in sheet.columns:
            max_length = 0
            column_letter = column[0].column_letter
            
            for cell in column:
                if cell.value:
                    max_length = max(max_length, len(str(cell.value)))
            
            adjusted_width = max_length + 2
            sheet.column_dimensions[column_letter].width = adjusted_width
    
    # Save to BytesIO object
    output = BytesIO()
    wb.save(output)
    output.seek(0)
    
    # Return response
    response = HttpResponse(
        output.read(),
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
    response['Content-Disposition'] = f'attachment; filename="{request.user.username}_attendance.xlsx"'
    
    return response

# 📤 EXPORT ADMIN CSV (ALL USERS)
@user_passes_test(lambda u: u.is_superuser)
def admin_export_csv(request):
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="all_attendance_logs.csv"'

    writer = csv.writer(response)
    writer.writerow([
        'Username', 'Date', 'Action', 'Time', 
        'Break 1 Minutes Remaining', 'Break 1 Minutes Exceeded',
        'Break 2 Minutes Remaining', 'Break 2 Minutes Exceeded',
        'Lunch Minutes Remaining', 'Lunch Minutes Exceeded', 
        'Note'
    ])
    
    logs = AttendanceLog.objects.select_related('user').order_by('timestamp')
    
    for log in logs:
        local_time = timezone.localtime(log.timestamp).strftime('%I:%M %p')
        writer.writerow([
            log.user.username,
            log.timestamp.date(),
            log.action,
            local_time,
            '', '', '', '', '', '', log.note or ''
        ])
    
    return response

# Automatic clock out function
def auto_clock_out_at_shift_end():
    """
    Automatically clock out users at 7 AM if they're still clocked in,
    end any ongoing breaks or lunch, and reset break/lunch allocations for the new day
    """
    now = timezone.now()
    manila_tz = pytz.timezone('Asia/Manila')
    local_now = now.astimezone(manila_tz)
    current_time = local_now.time()
    current_date = local_now.date()
    
    # Only run this between 7:00-7:10 AM
    if current_time >= time(7, 0) and current_time < time(7, 10):
        # Get all users in the system
        all_users = User.objects.filter(is_active=True)
        
        # 1. First, let's end any ongoing breaks or lunch for ALL users
        for user in all_users:
            today_allocation = DailyTimeAllocation.objects.filter(
                user=user,
                date=current_date
            ).first()
            
            if today_allocation:
                # Check and end Break 1 if active
                if today_allocation.break1_start_time:
                    break_duration = now - today_allocation.break1_start_time
                    minutes_used = int(break_duration.total_seconds() // 60)
                    today_allocation.break1_minutes_used += minutes_used
                    
                    # Log break end
                    break_log = AttendanceLog(
                        user=user,
                        action='end_break1',
                        note=f'Auto-ended break at shift end. Duration: {minutes_used} minutes'
                    )
                    break_log.save()
                    today_allocation.break1_start_time = None
                
                # Check and end Break 2 if active
                if today_allocation.break2_start_time:
                    break_duration = now - today_allocation.break2_start_time
                    minutes_used = int(break_duration.total_seconds() // 60)
                    today_allocation.break2_minutes_used += minutes_used
                    
                    # Log break end
                    break_log = AttendanceLog(
                        user=user,
                        action='end_break2',
                        note=f'Auto-ended break at shift end. Duration: {minutes_used} minutes'
                    )
                    break_log.save()
                    today_allocation.break2_start_time = None
                
                # Check and end Lunch if active
                if today_allocation.lunch_start_time:
                    lunch_duration = now - today_allocation.lunch_start_time
                    minutes_used = int(lunch_duration.total_seconds() // 60)
                    today_allocation.lunch_minutes_used += minutes_used
                    
                    # Log lunch end
                    lunch_log = AttendanceLog(
                        user=user,
                        action='end_lunch',
                        note=f'Auto-ended lunch at shift end. Duration: {minutes_used} minutes'
                    )
                    lunch_log.save()
                    today_allocation.lunch_start_time = None
                
                # Save the updated allocation
                today_allocation.save()

        # 2. Now, find users who are still clocked in and clock them out
        yesterday = current_date - timedelta(days=1)
        
        # Check for yesterday's clock-ins
        yesterday_clock_ins = AttendanceLog.objects.filter(
            action='clock_in',
            timestamp__date=yesterday
        ).select_related('user')
        
        # Check for today's early morning clock-ins (before 7 AM)
        today_early_clock_ins = AttendanceLog.objects.filter(
            action='clock_in',
            timestamp__date=current_date,
            timestamp__time__lt=time(7, 0)
        ).select_related('user')
        
        # For each potential clocked-in user, check if they've clocked out
        potential_users = set()
        for log in list(yesterday_clock_ins) + list(today_early_clock_ins):
            potential_users.add(log.user)
        
        for user in potential_users:
            # Check if the user has a clock-out entry after their last clock-in
            last_clock_in = AttendanceLog.objects.filter(
                user=user,
                action='clock_in',
                timestamp__date__gte=yesterday,
                timestamp__date__lte=current_date
            ).order_by('-timestamp').first()
            
            if last_clock_in:
                last_clock_out = AttendanceLog.objects.filter(
                    user=user,
                    action='clock_out',
                    timestamp__gt=last_clock_in.timestamp
                ).first()
                
                if not last_clock_out:
                    # Create clock-out log - they're still clocked in
                    log = AttendanceLog(
                        user=user, 
                        action='clock_out', 
                        note='Automatic clock-out at shift end (7 AM)'
                    )
                    log.save()
        
        # 3. Reset all break/lunch allocations for the new day
        # Get all allocations for today
        today_allocations = DailyTimeAllocation.objects.filter(date=current_date)
        
        for allocation in today_allocations:
            # Reset to fresh allocation values for the new day
            allocation.break1_minutes_used = 0
            allocation.break2_minutes_used = 0
            allocation.lunch_minutes_used = 0
            allocation.break1_start_time = None
            allocation.break2_start_time = None
            allocation.lunch_start_time = None
            allocation.save()
            
        return f"Auto clock-out process completed. Ended breaks, clocked out remaining users, and reset allocations."
    else:
        return "Auto clock-out process skipped - not between 7:00-7:10 AM."
