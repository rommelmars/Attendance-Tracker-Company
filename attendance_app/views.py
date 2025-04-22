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
            
            # Get Manila timezone
            manila_tz = pytz.timezone('Asia/Manila')
            now_manila = now.astimezone(manila_tz)
            
            # Calculate if late
            is_late = False
            late_minutes = 0
            
            # Determine shift date - if before 7 AM, shift is for previous day
            shift_date = now_manila.date()
            if now_manila.hour < 7:
                shift_date = shift_date - timedelta(days=1)
            
            # Calculate shift start time (10 PM on shift date)
            shift_start_time = time(22, 0)
            shift_start_datetime = datetime.combine(shift_date, shift_start_time)
            shift_start_datetime = pytz.timezone('Asia/Manila').localize(shift_start_datetime)
            
            # Check if clocked in after shift start time
            if now_manila > shift_start_datetime:
                is_late = True
                time_diff = now_manila - shift_start_datetime
                late_minutes = int(time_diff.total_seconds() // 60)
            
            # Create the log with late information if applicable
            note = None
            if is_late:
                note = f"Late arrival: {late_minutes} minutes"
            
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
    from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
    from openpyxl.utils import get_column_letter
    from io import BytesIO
    
    # Create a new workbook and sheets
    wb = openpyxl.Workbook()
    
    # Create the sheets we need with valid names
    clock_sheet = wb.active
    clock_sheet.title = "Clock In-Out"
    break_sheet = wb.create_sheet(title="Breaks")
    lunch_sheet = wb.create_sheet(title="Lunch")
    summary_sheet = wb.create_sheet(title="Summary")
    
    # Define styles
    header_font = Font(bold=True, size=12)
    header_fill = PatternFill(start_color="4F81BD", end_color="4F81BD", fill_type="solid")
    header_alignment = Alignment(horizontal='center', vertical='center')
    
    date_fill = PatternFill(start_color="DCE6F1", end_color="DCE6F1", fill_type="solid")
    alt_row_fill = PatternFill(start_color="F2F2F2", end_color="F2F2F2", fill_type="solid")
    
    exceeded_font = Font(color="FF0000", bold=True)
    
    thin_border = Border(
        left=Side(style='thin'),
        right=Side(style='thin'),
        top=Side(style='thin'),
        bottom=Side(style='thin')
    )
    
    # Set up headers
    headers = {
        "Clock In-Out": ["Date", "Action", "Time", "Note", "Late (minutes)"],
        "Breaks": ["Date", "Action", "Time", "Break Type", "Duration (min)", "Remaining (min)", "Exceeded (min)", "Note"],
        "Lunch": ["Date", "Action", "Time", "Duration (min)", "Remaining (min)", "Exceeded (min)", "Note"],
        "Summary": ["Date", "Status", "Clock In Time", "Clock Out Time", "Total Hours", 
                   "Break 1 Used", "Break 2 Used", "Lunch Used", "Break 1 Exceeded", 
                   "Break 2 Exceeded", "Lunch Exceeded", "Late Minutes"]
    }
    
    # Apply headers to each sheet with styling
    for sheet_name, sheet in [("Clock In-Out", clock_sheet), ("Breaks", break_sheet), 
                             ("Lunch", lunch_sheet), ("Summary", summary_sheet)]:
        for col_num, header in enumerate(headers[sheet_name], 1):
            cell = sheet.cell(row=1, column=col_num)
            cell.value = header
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = header_alignment
            cell.border = thin_border
    
    # Get all logs for the user
    logs = AttendanceLog.objects.filter(user=request.user).order_by('timestamp')
    
    # Track row numbers for each sheet
    clock_row = 2
    break_row = 2
    lunch_row = 2
    summary_row = 2
    
    # Group logs by date
    dates = set(log.timestamp.date() for log in logs)
    
    # Dictionary to store daily summary data
    daily_summary = {}
    
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
        break1_used = 0
        break2_used = 0
        lunch_used = 0
        
        if time_allocation:
            break1_remaining = time_allocation.break1_minutes_remaining()
            break2_remaining = time_allocation.break2_minutes_remaining()
            lunch_remaining = time_allocation.lunch_minutes_remaining()
            break1_exceeded = time_allocation.break1_minutes_exceeded()
            break2_exceeded = time_allocation.break2_minutes_exceeded()
            lunch_exceeded = time_allocation.lunch_minutes_exceeded()
            break1_used = time_allocation.break1_minutes_used
            break2_used = time_allocation.break2_minutes_used
            lunch_used = time_allocation.lunch_minutes_used
            
        # Initialize summary data for this date
        daily_summary[log_date] = {
            'status': 'Incomplete',
            'clock_in_time': None,
            'clock_out_time': None,
            'total_hours': 0,
            'break1_used': break1_used,
            'break2_used': break2_used,
            'lunch_used': lunch_used,
            'break1_exceeded': break1_exceeded,
            'break2_exceeded': break2_exceeded,
            'lunch_exceeded': lunch_exceeded,
            'late_minutes': 0
        }
            
        # Get logs for this specific date
        daily_logs = logs.filter(timestamp__date=log_date)
        
        # Find first clock in and last clock out
        first_clock_in = daily_logs.filter(action='clock_in').order_by('timestamp').first()
        last_clock_out = daily_logs.filter(action='clock_out').order_by('-timestamp').first()
        
        if first_clock_in:
            daily_summary[log_date]['clock_in_time'] = timezone.localtime(first_clock_in.timestamp)
            # Check for late arrival
            if first_clock_in.note and 'Late arrival:' in first_clock_in.note:
                try:
                    minutes_late = int(first_clock_in.note.split('Late arrival:')[1].split('minutes')[0].strip())
                    daily_summary[log_date]['late_minutes'] = minutes_late
                except (ValueError, IndexError):
                    pass
        
        if last_clock_out:
            daily_summary[log_date]['clock_out_time'] = timezone.localtime(last_clock_out.timestamp)
        
        # Calculate total hours if both clock in and out exist
        if daily_summary[log_date]['clock_in_time'] and daily_summary[log_date]['clock_out_time']:
            daily_summary[log_date]['status'] = 'Complete'
            time_diff = daily_summary[log_date]['clock_out_time'] - daily_summary[log_date]['clock_in_time']
            # Convert to decimal hours (e.g., 7.5 hours)
            daily_summary[log_date]['total_hours'] = round(time_diff.total_seconds() / 3600, 2)
        
        # Apply alternate row styling
        row_fill = alt_row_fill if summary_row % 2 == 0 else None
        
        for log in daily_logs:
            # Format the time in local timezone with 12-hour AM/PM format
            local_time = timezone.localtime(log.timestamp).strftime('%I:%M %p')
            
            # Process based on action type
            if log.action in ['clock_in', 'clock_out']:
                # Apply styling based on row number
                row_style = alt_row_fill if clock_row % 2 == 0 else None
                
                # Add to Clock In-Out sheet
                for col in range(1, 6):
                    cell = clock_sheet.cell(row=clock_row, column=col)
                    cell.border = thin_border
                    if row_style:
                        cell.fill = row_style
                
                clock_sheet.cell(row=clock_row, column=1).value = log_date.strftime('%Y-%m-%d')
                clock_sheet.cell(row=clock_row, column=2).value = log.action.replace('_', ' ').title()
                clock_sheet.cell(row=clock_row, column=3).value = local_time
                clock_sheet.cell(row=clock_row, column=4).value = log.note or ''
                
                # Extract late minutes from note if it's a clock_in
                if log.action == 'clock_in' and log.note and 'Late arrival:' in log.note:
                    try:
                        minutes_late = int(log.note.split('Late arrival:')[1].split('minutes')[0].strip())
                        clock_sheet.cell(row=clock_row, column=5).value = minutes_late
                        clock_sheet.cell(row=clock_row, column=5).font = exceeded_font
                    except (ValueError, IndexError):
                        pass
                    
                clock_row += 1
                
            elif log.action in ['start_break1', 'end_break1', 'start_break2', 'end_break2']:
                # Apply styling based on row number
                row_style = alt_row_fill if break_row % 2 == 0 else None
                
                # Add to Breaks sheet
                for col in range(1, 9):
                    cell = break_sheet.cell(row=break_row, column=col)
                    cell.border = thin_border
                    if row_style:
                        cell.fill = row_style
                
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
                        break_sheet.cell(row=break_row, column=7).font = exceeded_font
                else:  # break2
                    break_sheet.cell(row=break_row, column=6).value = break2_remaining
                    if 'end' in log.action and break2_exceeded > 0:
                        break_sheet.cell(row=break_row, column=7).value = break2_exceeded
                        break_sheet.cell(row=break_row, column=7).font = exceeded_font
                
                break_sheet.cell(row=break_row, column=8).value = log.note or ''
                break_row += 1
                
            elif log.action in ['start_lunch', 'end_lunch']:
                # Apply styling based on row number
                row_style = alt_row_fill if lunch_row % 2 == 0 else None
                
                # Add to Lunch sheet
                for col in range(1, 8):
                    cell = lunch_sheet.cell(row=lunch_row, column=col)
                    cell.border = thin_border
                    if row_style:
                        cell.fill = row_style
                
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
                    lunch_sheet.cell(row=lunch_row, column=6).font = exceeded_font
                
                lunch_sheet.cell(row=lunch_row, column=7).value = log.note or ''
                lunch_row += 1
    
    # Fill summary sheet with daily data
    for log_date in sorted(daily_summary.keys()):
        data = daily_summary[log_date]
        
        # Apply styling based on row number
        row_style = alt_row_fill if summary_row % 2 == 0 else None
        
        for col in range(1, 13):
            cell = summary_sheet.cell(row=summary_row, column=col)
            cell.border = thin_border
            if row_style:
                cell.fill = row_style
        
        summary_sheet.cell(row=summary_row, column=1).value = log_date.strftime('%Y-%m-%d')
        summary_sheet.cell(row=summary_row, column=2).value = data['status']
        
        # Format clock times nicely
        if data['clock_in_time']:
            summary_sheet.cell(row=summary_row, column=3).value = data['clock_in_time'].strftime('%I:%M %p')
        if data['clock_out_time']:
            summary_sheet.cell(row=summary_row, column=4).value = data['clock_out_time'].strftime('%I:%M %p')
            
        summary_sheet.cell(row=summary_row, column=5).value = data['total_hours']
        summary_sheet.cell(row=summary_row, column=6).value = data['break1_used']
        summary_sheet.cell(row=summary_row, column=7).value = data['break2_used']
        summary_sheet.cell(row=summary_row, column=8).value = data['lunch_used']
        
        # Highlight exceeded times
        if data['break1_exceeded'] > 0:
            summary_sheet.cell(row=summary_row, column=9).value = data['break1_exceeded']
            summary_sheet.cell(row=summary_row, column=9).font = exceeded_font
        else:
            summary_sheet.cell(row=summary_row, column=9).value = 0
            
        if data['break2_exceeded'] > 0:
            summary_sheet.cell(row=summary_row, column=10).value = data['break2_exceeded']
            summary_sheet.cell(row=summary_row, column=10).font = exceeded_font
        else:
            summary_sheet.cell(row=summary_row, column=10).value = 0
            
        if data['lunch_exceeded'] > 0:
            summary_sheet.cell(row=summary_row, column=11).value = data['lunch_exceeded']
            summary_sheet.cell(row=summary_row, column=11).font = exceeded_font
        else:
            summary_sheet.cell(row=summary_row, column=11).value = 0
        
        # Add late minutes if any
        if data['late_minutes'] > 0:
            summary_sheet.cell(row=summary_row, column=12).value = data['late_minutes']
            summary_sheet.cell(row=summary_row, column=12).font = exceeded_font
        else:
            summary_sheet.cell(row=summary_row, column=12).value = 0
            
        summary_row += 1
    
    # Add totals row to summary sheet
    summary_sheet.cell(row=summary_row, column=1).value = "TOTALS"
    summary_sheet.cell(row=summary_row, column=1).font = header_font
    
    # Calculate total hours worked
    total_hours_formula = f"=SUM(E2:E{summary_row-1})"
    summary_sheet.cell(row=summary_row, column=5).value = total_hours_formula
    
    # Calculate total break/lunch times
    summary_sheet.cell(row=summary_row, column=6).value = f"=SUM(F2:F{summary_row-1})"
    summary_sheet.cell(row=summary_row, column=7).value = f"=SUM(G2:G{summary_row-1})"
    summary_sheet.cell(row=summary_row, column=8).value = f"=SUM(H2:H{summary_row-1})"
    
    # Calculate total exceeded times
    summary_sheet.cell(row=summary_row, column=9).value = f"=SUM(I2:I{summary_row-1})"
    summary_sheet.cell(row=summary_row, column=10).value = f"=SUM(J2:J{summary_row-1})"
    summary_sheet.cell(row=summary_row, column=11).value = f"=SUM(K2:K{summary_row-1})"
    
    # Calculate total late minutes
    summary_sheet.cell(row=summary_row, column=12).value = f"=SUM(L2:L{summary_row-1})"
    
    # Style the totals row
    for col in range(1, 13):
        cell = summary_sheet.cell(row=summary_row, column=col)
        cell.font = header_font
        cell.border = thin_border
        cell.fill = header_fill
    
    # Auto-adjust column widths for better readability
    for sheet in [clock_sheet, break_sheet, lunch_sheet, summary_sheet]:
        for column in sheet.columns:
            max_length = 0
            column_letter = column[0].column_letter
            
            for cell in column:
                if cell.value:
                    max_length = max(max_length, len(str(cell.value)))
            
            adjusted_width = max(max_length + 2, 12)  # Minimum width of 12
            sheet.column_dimensions[column_letter].width = adjusted_width
    
    # Set print area and page setup for each sheet
    for sheet in [summary_sheet, clock_sheet, break_sheet, lunch_sheet]:
        sheet.page_setup.orientation = sheet.ORIENTATION_LANDSCAPE
        sheet.page_setup.fitToWidth = True
        sheet.print_title_rows = '1:1'  # Repeat header row on each page
    
    # Save to BytesIO object
    output = BytesIO()
    wb.save(output)
    output.seek(0)
    
    # Return response with the current month and year in the filename
    current_month_year = datetime.now().strftime('%B_%Y')
    filename = f"{request.user.username}_attendance_{current_month_year}.xlsx"
    
    response = HttpResponse(
        output.read(),
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    
    return response

# 📤 EXPORT ADMIN CSV (ALL USERS)
@user_passes_test(lambda u: u.is_superuser)
def admin_export_csv(request):
    # Import necessary libraries
    import openpyxl
    from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
    from openpyxl.utils import get_column_letter
    from io import BytesIO
    
    # Get specific user parameter if provided
    selected_user_id = request.GET.get('user')
    
    # Create a new workbook
    wb = openpyxl.Workbook()
    
    # Create the sheets we need with valid names
    summary_sheet = wb.active
    summary_sheet.title = "Summary"
    
    # Define styles
    header_font = Font(bold=True, size=12, color='FFFFFF')
    header_fill = PatternFill(start_color="4F81BD", end_color="4F81BD", fill_type="solid")
    header_alignment = Alignment(horizontal='center', vertical='center')
    
    date_fill = PatternFill(start_color="DCE6F1", end_color="DCE6F1", fill_type="solid")
    alt_row_fill = PatternFill(start_color="F2F2F2", end_color="F2F2F2", fill_type="solid")
    
    exceeded_font = Font(color="FF0000", bold=True)
    
    thin_border = Border(
        left=Side(style='thin'),
        right=Side(style='thin'),
        top=Side(style='thin'),
        bottom=Side(style='thin')
    )
    
    # Set up headers for summary sheet
    headers = ["Username", "Date", "Status", "Clock In Time", "Clock Out Time", "Total Hours", 
              "Break 1 Used", "Break 2 Used", "Lunch Used", "Break 1 Exceeded", 
              "Break 2 Exceeded", "Lunch Exceeded", "Late Minutes"]
    
    # Apply headers to summary sheet with styling
    for col_num, header in enumerate(headers, 1):
        cell = summary_sheet.cell(row=1, column=col_num)
        cell.value = header
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = header_alignment
        cell.border = thin_border
    
    # Get data depending on whether we're showing all users or a specific user
    if selected_user_id:
        users = User.objects.filter(id=selected_user_id)
        filename_prefix = f"user_{users.first().username}_attendance"
    else:
        users = User.objects.all().order_by('username')
        filename_prefix = "all_users_attendance"
    
    # Track the current row in the summary sheet
    summary_row = 2
    
    # Process each user's data
    for user in users:
        # Get all logs for the user
        logs = AttendanceLog.objects.filter(user=user).order_by('timestamp')
        
        # If user has no logs, continue to next user
        if not logs.exists():
            continue
        
        # Create a separate sheet for detailed logs for this user
        user_sheet_name = f"{user.username[:20]}"  # Limit to 20 chars for Excel sheet name limit
        user_sheet = wb.create_sheet(title=user_sheet_name)
        
        # Set up headers for user sheet
        user_headers = ["Date", "Action", "Time", "Note", "Break/Lunch Status"]
        
        for col_num, header in enumerate(user_headers, 1):
            cell = user_sheet.cell(row=1, column=col_num)
            cell.value = header
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = header_alignment
            cell.border = thin_border
        
        # Group logs by date
        dates = set(log.timestamp.date() for log in logs)
        
        # Dictionary to store daily summary data
        daily_summary = {}
        
        # Track the current row in the user sheet
        user_row = 2
        
        # Process each date's logs
        for log_date in sorted(dates):
            # Get the time allocation for this date
            time_allocation = DailyTimeAllocation.objects.filter(
                user=user,
                date=log_date
            ).first()
            
            # If no time allocation exists for this date, create default values
            break1_remaining = 15
            break2_remaining = 15
            lunch_remaining = 60
            break1_exceeded = 0
            break2_exceeded = 0
            lunch_exceeded = 0
            break1_used = 0
            break2_used = 0
            lunch_used = 0
            
            if time_allocation:
                break1_remaining = time_allocation.break1_minutes_remaining()
                break2_remaining = time_allocation.break2_minutes_remaining()
                lunch_remaining = time_allocation.lunch_minutes_remaining()
                break1_exceeded = time_allocation.break1_minutes_exceeded()
                break2_exceeded = time_allocation.break2_minutes_exceeded()
                lunch_exceeded = time_allocation.lunch_minutes_exceeded()
                break1_used = time_allocation.break1_minutes_used
                break2_used = time_allocation.break2_minutes_used
                lunch_used = time_allocation.lunch_minutes_used
                
            # Initialize summary data for this date
            daily_summary[log_date] = {
                'status': 'Incomplete',
                'clock_in_time': None,
                'clock_out_time': None,
                'total_hours': 0,
                'break1_used': break1_used,
                'break2_used': break2_used,
                'lunch_used': lunch_used,
                'break1_exceeded': break1_exceeded,
                'break2_exceeded': break2_exceeded,
                'lunch_exceeded': lunch_exceeded,
                'late_minutes': 0
            }
                
            # Get logs for this specific date
            daily_logs = logs.filter(timestamp__date=log_date)
            
            # Find first clock in and last clock out
            first_clock_in = daily_logs.filter(action='clock_in').order_by('timestamp').first()
            last_clock_out = daily_logs.filter(action='clock_out').order_by('-timestamp').first()
            
            if first_clock_in:
                daily_summary[log_date]['clock_in_time'] = timezone.localtime(first_clock_in.timestamp)
                # Check for late arrival
                if first_clock_in.note and 'Late arrival:' in first_clock_in.note:
                    try:
                        minutes_late = int(first_clock_in.note.split('Late arrival:')[1].split('minutes')[0].strip())
                        daily_summary[log_date]['late_minutes'] = minutes_late
                    except (ValueError, IndexError):
                        pass
            
            if last_clock_out:
                daily_summary[log_date]['clock_out_time'] = timezone.localtime(last_clock_out.timestamp)
            
            # Calculate total hours if both clock in and out exist
            if daily_summary[log_date]['clock_in_time'] and daily_summary[log_date]['clock_out_time']:
                daily_summary[log_date]['status'] = 'Complete'
                time_diff = daily_summary[log_date]['clock_out_time'] - daily_summary[log_date]['clock_in_time']
                # Convert to decimal hours (e.g., 7.5 hours)
                daily_summary[log_date]['total_hours'] = round(time_diff.total_seconds() / 3600, 2)
            
            # Add this date to the summary sheet
            data = daily_summary[log_date]
            
            # Apply styling based on row number
            row_style = alt_row_fill if summary_row % 2 == 0 else None
            
            for col in range(1, 14):
                cell = summary_sheet.cell(row=summary_row, column=col)
                cell.border = thin_border
                if row_style:
                    cell.fill = row_style
            
            # Username
            summary_sheet.cell(row=summary_row, column=1).value = user.username
            
            # Date
            summary_sheet.cell(row=summary_row, column=2).value = log_date.strftime('%Y-%m-%d')
            
            # Status
            summary_sheet.cell(row=summary_row, column=3).value = data['status']
            
            # Format clock times nicely
            if data['clock_in_time']:
                summary_sheet.cell(row=summary_row, column=4).value = data['clock_in_time'].strftime('%I:%M %p')
            if data['clock_out_time']:
                summary_sheet.cell(row=summary_row, column=5).value = data['clock_out_time'].strftime('%I:%M %p')
                
            # Hours worked
            summary_sheet.cell(row=summary_row, column=6).value = data['total_hours']
            
            # Break and lunch times
            summary_sheet.cell(row=summary_row, column=7).value = data['break1_used']
            summary_sheet.cell(row=summary_row, column=8).value = data['break2_used']
            summary_sheet.cell(row=summary_row, column=9).value = data['lunch_used']
            
            # Highlight exceeded times
            if data['break1_exceeded'] > 0:
                summary_sheet.cell(row=summary_row, column=10).value = data['break1_exceeded']
                summary_sheet.cell(row=summary_row, column=10).font = exceeded_font
            else:
                summary_sheet.cell(row=summary_row, column=10).value = 0
                
            if data['break2_exceeded'] > 0:
                summary_sheet.cell(row=summary_row, column=11).value = data['break2_exceeded']
                summary_sheet.cell(row=summary_row, column=11).font = exceeded_font
            else:
                summary_sheet.cell(row=summary_row, column=11).value = 0
                
            if data['lunch_exceeded'] > 0:
                summary_sheet.cell(row=summary_row, column=12).value = data['lunch_exceeded']
                summary_sheet.cell(row=summary_row, column=12).font = exceeded_font
            else:
                summary_sheet.cell(row=summary_row, column=12).value = 0
            
            # Add late minutes if any
            if data['late_minutes'] > 0:
                summary_sheet.cell(row=summary_row, column=13).value = data['late_minutes']
                summary_sheet.cell(row=summary_row, column=13).font = exceeded_font
            else:
                summary_sheet.cell(row=summary_row, column=13).value = 0
                
            summary_row += 1
            
            # Add detailed logs to user sheet
            for log in daily_logs:
                # Format the time in local timezone with 12-hour AM/PM format
                local_time = timezone.localtime(log.timestamp).strftime('%I:%M %p')
                
                # Apply styling based on row number
                row_style = alt_row_fill if user_row % 2 == 0 else None
                
                for col in range(1, 6):
                    cell = user_sheet.cell(row=user_row, column=col)
                    cell.border = thin_border
                    if row_style:
                        cell.fill = row_style
                
                # Date
                user_sheet.cell(row=user_row, column=1).value = log_date.strftime('%Y-%m-%d')
                
                # Action - format nicely
                user_sheet.cell(row=user_row, column=2).value = log.action.replace('_', ' ').title()
                
                # Time
                user_sheet.cell(row=user_row, column=3).value = local_time
                
                # Note
                user_sheet.cell(row=user_row, column=4).value = log.note or ''
                
                # Break/Lunch Status - add status information for break/lunch ends
                if log.action in ['end_break1', 'end_break2', 'end_lunch']:
                    status_text = ''
                    if log.action == 'end_break1' and break1_exceeded > 0:
                        status_text = f'Exceeded by {break1_exceeded} min'
                        user_sheet.cell(row=user_row, column=5).font = exceeded_font
                    elif log.action == 'end_break2' and break2_exceeded > 0:
                        status_text = f'Exceeded by {break2_exceeded} min'
                        user_sheet.cell(row=user_row, column=5).font = exceeded_font
                    elif log.action == 'end_lunch' and lunch_exceeded > 0:
                        status_text = f'Exceeded by {lunch_exceeded} min'
                        user_sheet.cell(row=user_row, column=5).font = exceeded_font
                    else:
                        if log.action == 'end_break1':
                            status_text = f'{break1_remaining} min remaining'
                        elif log.action == 'end_break2':
                            status_text = f'{break2_remaining} min remaining'
                        elif log.action == 'end_lunch':
                            status_text = f'{lunch_remaining} min remaining'
                            
                    user_sheet.cell(row=user_row, column=5).value = status_text
                
                user_row += 1
        
        # Auto-adjust column widths for user sheet
        for column in user_sheet.columns:
            max_length = 0
            column_letter = column[0].column_letter
            
            for cell in column:
                if cell.value:
                    max_length = max(max_length, len(str(cell.value)))
            
            adjusted_width = max(max_length + 2, 12)  # Minimum width of 12
            user_sheet.column_dimensions[column_letter].width = adjusted_width
        
        # Set print area and page setup for user sheet
        user_sheet.page_setup.orientation = user_sheet.ORIENTATION_LANDSCAPE
        user_sheet.page_setup.fitToWidth = True
        user_sheet.print_title_rows = '1:1'  # Repeat header row on each page
    
    # Add totals row to summary sheet if we have more than one row of data
    if summary_row > 2:
        # Style the totals row
        for col in range(1, 14):
            cell = summary_sheet.cell(row=summary_row, column=col)
            cell.font = header_font
            cell.border = thin_border
            cell.fill = header_fill
        
        # Add "TOTALS" text
        summary_sheet.cell(row=summary_row, column=1).value = "TOTALS"
        summary_sheet.merge_cells(start_row=summary_row, start_column=1, end_row=summary_row, end_column=3)
        summary_sheet.cell(row=summary_row, column=1).alignment = Alignment(horizontal='center')
        
        # Add formulas for totals
        summary_sheet.cell(row=summary_row, column=6).value = f"=SUM(F2:F{summary_row-1})"  # Total hours
        summary_sheet.cell(row=summary_row, column=7).value = f"=SUM(G2:G{summary_row-1})"  # Break1 used
        summary_sheet.cell(row=summary_row, column=8).value = f"=SUM(H2:H{summary_row-1})"  # Break2 used
        summary_sheet.cell(row=summary_row, column=9).value = f"=SUM(I2:I{summary_row-1})"  # Lunch used
        summary_sheet.cell(row=summary_row, column=10).value = f"=SUM(J2:J{summary_row-1})"  # Break1 exceeded
        summary_sheet.cell(row=summary_row, column=11).value = f"=SUM(K2:K{summary_row-1})"  # Break2 exceeded
        summary_sheet.cell(row=summary_row, column=12).value = f"=SUM(L2:L{summary_row-1})"  # Lunch exceeded
        summary_sheet.cell(row=summary_row, column=13).value = f"=SUM(M2:M{summary_row-1})"  # Late minutes
    
    # Auto-adjust column widths for summary sheet
    for column in summary_sheet.columns:
        max_length = 0
        column_letter = column[0].column_letter
        
        for cell in column:
            if cell.value:
                max_length = max(max_length, len(str(cell.value)))
        
        adjusted_width = max(max_length + 2, 12)  # Minimum width of 12
        summary_sheet.column_dimensions[column_letter].width = adjusted_width
    
    # Set summary sheet as the active sheet
    wb.active = summary_sheet
    
    # Save to BytesIO object
    output = BytesIO()
    wb.save(output)
    output.seek(0)
    
    # Return response with appropriate filename
    current_month_year = datetime.now().strftime('%B_%Y')
    filename = f"{filename_prefix}_{current_month_year}.xlsx"
    
    response = HttpResponse(
        output.read(),
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    
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
