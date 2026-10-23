import json
import datetime
import hashlib
import uuid
from pathlib import Path
from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse, HttpResponse, HttpResponseBadRequest, StreamingHttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator
from django.contrib.auth import logout as django_logout
from django.db.models import Count

from voice_tutor import models
from services.elevenlabs_service import get_elevenlabs_service
from services.mem0_service import get_mem0_service
from services.tutor_agent_service import get_tutor_agent_service
from services.openai_voice_service import get_openai_voice_service
from database.db import get_database
from utils.logger import get_logger

DEFAULT_MEM0_USER_ID = '5033a22d-170c-4ac8-93ec-bf39035cdadb'
logger = get_logger(__name__)

# Helper: Check if user is logged in
def get_session_user(request):
    return request.session.get('user', None)

# Helper: Decorator for requiring login
def login_required_custom(role=None):
    def decorator(view_func):
        def _wrapped_view(request, *args, **kwargs):
            user = get_session_user(request)
            if not user:
                return redirect('voice_tutor:login')
            if role and user.get('role') != role:
                return redirect('voice_tutor:professor_dashboard' if user.get('role') == 'professor' else 'voice_tutor:student_dashboard')
            
            response = view_func(request, *args, **kwargs)
            # Add cache-control headers to prevent caching of authenticated pages
            if isinstance(response, HttpResponse):
                response['Cache-Control'] = 'no-cache, no-store, must-revalidate, max-age=0'
                response['Pragma'] = 'no-cache'
                response['Expires'] = '0'
            return response
        return _wrapped_view
    return decorator

# Helper to serialize subjects/topics
def get_serialized_subjects():
    subjects_list = []
    for s in models.SubjectMaster.objects.all():
        topics_list = []
        for t in s.topics.all():
            learning_objs = []
            if t.learning_objectives:
                try:
                    learning_objs = json.loads(t.learning_objectives)
                except Exception:
                    pass
            
            videos_list = []
            for v in t.tutorial_videos.all():
                videos_list.append({
                    'id': v.code or f"VID{v.id}",
                    'title': v.title,
                    'src': v.src,
                    'description': v.description or ''
                })
            
            # Find guided assignments for this topic
            guided_ids = [a.code for a in t.assignments.exclude(code__startswith='A_PRACTICE')]
            
            # Practice / Assignment questions
            questions_list = []
            # Gather questions from ALL assignments related to this topic
            for a in t.assignments.all():
                for q in a.questions.all().order_by('order'):
                    options_list = [opt.option_text for opt in q.options.all().order_by('option_letter')]
                    questions_list.append({
                        'id': q.code,
                        'content': q.content,
                        'type': q.question_type,
                        'options': options_list,
                        'correctAnswer': q.correct_answer,
                        'conceptTags': json.loads(q.concept_tags) if q.concept_tags else [],
                        'difficulty': q.difficulty
                    })
            
            topics_list.append({
                'id': t.code,
                'subjectId': s.code,
                'name': t.name,
                'description': t.description or '',
                'learningObjectives': learning_objs,
                'videos': videos_list,
                'guidedAssignmentIds': guided_ids,
                'assignmentQuestions': questions_list
            })
            
        subjects_list.append({
            'id': s.code,
            'name': s.name,
            'description': s.description or '',
            'topics': topics_list
        })
    return subjects_list

# --- Frontend HTML Views ---

def index_view(request):
    user = get_session_user(request)
    if user:
        if user.get('role') == 'professor':
            return redirect('voice_tutor:professor_dashboard')
        return redirect('voice_tutor:student_dashboard')
    return render(request, 'website_landing.html')

def auto_seed_if_empty():
    if models.SubjectMaster.objects.exists():
        return
    
    # 1. Roles
    student_role, _ = models.UserRoleMaster.objects.get_or_create(role_code='student', defaults={'role_name': 'Student'})
    prof_role, _ = models.UserRoleMaster.objects.get_or_create(role_code='professor', defaults={'role_name': 'Professor'})
    admin_role, _ = models.UserRoleMaster.objects.get_or_create(role_code='admin', defaults={'role_name': 'Admin'})

    # 2. University, Course, Session
    univ, _ = models.UniversityMaster.objects.get_or_create(code='INS', defaults={'name': 'University of InSync', 'address': 'Campus'})
    course, _ = models.CourseMaster.objects.get_or_create(code='CSE', defaults={'name': 'Computer Science', 'university': univ})
    session, _ = models.SessionMaster.objects.get_or_create(name='2024 to 2028', defaults={'start_year': 2024, 'end_year': 2028})

    # 3. Users & Profiles
    from django.contrib.auth.models import User
    
    # Professor
    prof_user, created_prof = User.objects.get_or_create(username='prof_001', defaults={'email': 'prof.sharma@university.edu'})
    if created_prof:
        prof_user.set_password('demo123')
        prof_user.save()
    prof_profile, _ = models.UserProfile.objects.get_or_create(
        user=prof_user,
        defaults={'name': 'Dr. Anita Sharma', 'email': 'prof.sharma@university.edu', 'role': prof_role, 'is_approved': True}
    )

    # Students
    s1_user, created_s1 = User.objects.get_or_create(username='student_001', defaults={'email': 'rahul.kumar@student.edu'})
    if created_s1:
        s1_user.set_password('demo123')
        s1_user.save()
    s1_profile, _ = models.UserProfile.objects.get_or_create(
        user=s1_user,
        defaults={'name': 'Rahul Kumar', 'email': 'rahul.kumar@student.edu', 'role': student_role, 'is_approved': True}
    )

    s2_user, created_s2 = User.objects.get_or_create(username='student_002', defaults={'email': 'priya.singh@student.edu'})
    if created_s2:
        s2_user.set_password('demo123')
        s2_user.save()
    s2_profile, _ = models.UserProfile.objects.get_or_create(
        user=s2_user,
        defaults={'name': 'Priya Singh', 'email': 'priya.singh@student.edu', 'role': student_role, 'is_approved': True}
    )

    s3_user, created_s3 = User.objects.get_or_create(username='student_003', defaults={'email': 'arjun.patel@student.edu'})
    if created_s3:
        s3_user.set_password('demo123')
        s3_user.save()
    s3_profile, _ = models.UserProfile.objects.get_or_create(
        user=s3_user,
        defaults={'name': 'Arjun Patel', 'email': 'arjun.patel@student.edu', 'role': student_role, 'is_approved': True}
    )

    # 4. Subject, Module, Topic
    subject, _ = models.SubjectMaster.objects.get_or_create(
        code='SUB001',
        defaults={
            'name': 'Machine Learning',
            'course': course,
            'session': session,
            'semester_or_year': 'Semester 5',
            'description': 'Explore ML fundamentals.'
        }
    )

    module, _ = models.ModuleMaster.objects.get_or_create(
        name='Ensemble Learning',
        defaults={'subject': subject, 'order': 1}
    )

    learning_objs = ['Understand ensemble learning', 'Differentiate bagging vs boosting']
    topic, _ = models.TopicMaster.objects.get_or_create(
        code='TOP001',
        defaults={
            'name': 'Bagging and Boosting',
            'module': module,
            'subject': subject,
            'description': 'Understand bagging and boosting.',
            'learning_objectives': json.dumps(learning_objs)
        }
    )

    # 5. Videos
    models.TutorialVideo.objects.get_or_create(
        topic=topic,
        code='VID001',
        defaults={'title': 'Bagging', 'src': '/videos/boosting.mp4'}
    )
    models.TutorialVideo.objects.get_or_create(
        topic=topic,
        code='VID002',
        defaults={'title': 'Boosting', 'src': '/videos/bagging.mp4'}
    )

    # 6. Assignments & Questions
    a1, _ = models.Assignment.objects.get_or_create(
        code='A001',
        defaults={
            'topic': topic,
            'title': 'Probability Basics',
            'description': 'Intro to probability.',
            'difficulty': 'beginner',
            'expected_duration': 30
        }
    )
    a2, _ = models.Assignment.objects.get_or_create(
        code='A002',
        defaults={
            'topic': topic,
            'title': 'Conditional Probability',
            'description': 'Intro to conditional probability.',
            'difficulty': 'intermediate',
            'expected_duration': 45
        }
    )
    a_practice, _ = models.Assignment.objects.get_or_create(
        code='A_PRACTICE_TOP001',
        defaults={
            'topic': topic,
            'title': 'Bagging & Boosting Practice',
            'description': 'Practice questions.',
            'difficulty': 'intermediate',
            'expected_duration': 40
        }
    )

    # Questions
    models.AssignmentQuestion.objects.get_or_create(
        assignment=a1,
        code='Q001',
        defaults={'content': 'Probability of red marble?', 'question_type': 'numerical', 'correct_answer': '0.5', 'order': 1}
    )
    bq1, _ = models.AssignmentQuestion.objects.get_or_create(
        assignment=a_practice,
        code='BQ001',
        defaults={'content': 'Bagging training datasets?', 'question_type': 'mcq', 'correct_answer': 'b', 'order': 1}
    )
    models.QuestionOption.objects.get_or_create(question=bq1, option_letter='b', defaults={'option_text': 'By random sampling with replacement'})

    # 7. Assessments (Progress)
    models.StudentTopicAssessmentReview.objects.get_or_create(
        student=s1_profile,
        topic=topic,
        assignment=a1,
        defaults={'score': 85, 'questions_completed': 3, 'total_questions': 4, 'time_spent': 1200, 'hints_used': 2, 'status': 'in_progress'}
    )
    models.StudentTopicAssessmentReview.objects.get_or_create(
        student=s2_profile,
        topic=topic,
        assignment=a1,
        defaults={'score': 92, 'questions_completed': 4, 'total_questions': 4, 'time_spent': 1500, 'hints_used': 1, 'status': 'completed'}
    )

    # 8. batchs
    batch, _ = models.Batch.objects.get_or_create(
        code='C101',
        defaults={'name': 'JEE Probability Batch – 2026', 'description': 'Intensive probability module for JEE aspirants'}
    )
    batch.students.add(s1_profile, s2_profile, s3_profile)
    
    # 9. Allocation
    models.ProfessorAllocation.objects.get_or_create(
        batch=batch,
        subject=subject,
        professor=prof_profile
    )

def login_view(request):
    auto_seed_if_empty()
    user = get_session_user(request)
    if user:
        if user.get('role') == 'professor':
            return redirect('voice_tutor:professor_dashboard')
        return redirect('voice_tutor:student_dashboard')

    if request.method == 'POST':
        email = request.POST.get('email', '').strip()
        role = request.POST.get('role', '').strip()
        password = request.POST.get('password', '').strip()
        
        # Check database users
        profile = models.UserProfile.objects.filter(role__role_code=role, email=email).first()
        
        if profile and (profile.user.check_password(password) or not password):
            if not profile.is_approved:
                return render(request, 'login.html', {'error': 'Your account is pending administrator approval. You will be able to log in once approved.'})
            request.session['user'] = {
                'id': profile.user.username,
                'name': profile.name,
                'role': role,
                'email': email or profile.email,
                'createdAt': profile.created_at.isoformat()
            }
            return redirect('voice_tutor:professor_dashboard' if role == 'professor' else 'voice_tutor:student_dashboard')
        else:
            return render(request, 'login.html', {'error': 'Invalid email or password.'})
            
    return render(request, 'login.html')

def change_password_view(request):
    auto_seed_if_empty()
    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        current_password = request.POST.get('current_password', '').strip()
        new_password = request.POST.get('new_password', '').strip()
        confirm_password = request.POST.get('confirm_password', '').strip()

        if not username or not current_password or not new_password or not confirm_password:
            return render(request, 'login.html', {
                'cp_error': 'All fields are required for changing password.',
                'show_change_password': True
            })

        if new_password != confirm_password:
            return render(request, 'login.html', {
                'cp_error': 'New password and confirm password do not match.',
                'show_change_password': True
            })

        from django.contrib.auth.models import User
        user_obj = User.objects.filter(email=username).first() or User.objects.filter(username=username).first()
        
        if not user_obj:
            return render(request, 'login.html', {
                'cp_error': 'User with this username or email address does not exist.',
                'show_change_password': True
            })

        if not user_obj.check_password(current_password):
            return render(request, 'login.html', {
                'cp_error': 'Current password is incorrect.',
                'show_change_password': True
            })

        user_obj.set_password(new_password)
        user_obj.save()

        return render(request, 'login.html', {
            'success': 'Password changed successfully! Please sign in with your new password.',
            'show_change_password': False
        })

    return render(request, 'login.html', {'show_change_password': True})

def signup_view(request):
    auto_seed_if_empty()
    user = get_session_user(request)
    if user:
        if user.get('role') == 'professor':
            return redirect('voice_tutor:professor_dashboard')
        return redirect('voice_tutor:student_dashboard')

    if request.method == 'POST':
        role = request.POST.get('role', '').strip()
        name = request.POST.get('name', '').strip()
        email = request.POST.get('email', '').strip()
        password = request.POST.get('password', '').strip()
        mobile_number = request.POST.get('mobile_number', '').strip()
        dob_str = request.POST.get('dob', '').strip()
        university_id = request.POST.get('university', '').strip()
        emp_id = request.POST.get('emp_id', '').strip()
        enrollment_number = request.POST.get('enrollment_number', '').strip()
        preferred_subjects = request.POST.getlist('preferred_subjects')

        # Basic validation
        if not role or not name or not email or not mobile_number:
            return render(request, 'signup.html', {
                'error': 'Role, Name, Email, and Mobile Number are required fields.',
                'universities': models.UniversityMaster.objects.all(),
                'courses': models.CourseMaster.objects.all(),
                'sessions': models.SessionMaster.objects.all(),
                'subjects': models.SubjectMaster.objects.all()
            })

        import re
        if not re.match(r"[^@]+@[^@]+\.[^@]+", email):
            return render(request, 'signup.html', {
                'error': 'Please provide a valid email address.',
                'universities': models.UniversityMaster.objects.all(),
                'courses': models.CourseMaster.objects.all(),
                'sessions': models.SessionMaster.objects.all(),
                'subjects': models.SubjectMaster.objects.all()
            })

        if not re.match(r"^\+?1?\d{9,15}$", mobile_number):
            return render(request, 'signup.html', {
                'error': 'Please provide a valid mobile number.',
                'universities': models.UniversityMaster.objects.all(),
                'courses': models.CourseMaster.objects.all(),
                'sessions': models.SessionMaster.objects.all(),
                'subjects': models.SubjectMaster.objects.all()
            })

        if password and len(password) < 6:
            return render(request, 'signup.html', {
                'error': 'Password must be at least 6 characters long.',
                'universities': models.UniversityMaster.objects.all(),
                'courses': models.CourseMaster.objects.all(),
                'sessions': models.SessionMaster.objects.all(),
                'subjects': models.SubjectMaster.objects.all()
            })

        # Auto-generate password if blank: starting 5 digits of mobile_number + last 3 digits of Enrollment Number (or emp_id)
        if not password:
            clean_mobile = ''.join(filter(str.isdigit, mobile_number))
            mobile_prefix = clean_mobile[:5] if len(clean_mobile) >= 5 else clean_mobile
            clean_enr = ''.join(filter(str.isalnum, enrollment_number))
            clean_emp = ''.join(filter(str.isalnum, emp_id))
            enrollment_suffix = clean_enr[-3:] if len(clean_enr) >= 3 else (clean_emp[-3:] if len(clean_emp) >= 3 else '123')
            password = f"{mobile_prefix}{enrollment_suffix}"

        # Check unique constraints
        from django.contrib.auth.models import User
        if User.objects.filter(username=email).exists() or User.objects.filter(email=email).exists():
            return render(request, 'signup.html', {
                'error': 'Email address is already registered.',
                'universities': models.UniversityMaster.objects.all(),
                'courses': models.CourseMaster.objects.all(),
                'sessions': models.SessionMaster.objects.all(),
                'subjects': models.SubjectMaster.objects.all()
            })

        # Parse DOB (optional)
        dob = None
        if dob_str:
            try:
                dob = datetime.datetime.strptime(dob_str, '%Y-%m-%d').date()
            except Exception:
                pass

        try:
            # Create user with password
            user_obj = User.objects.create_user(username=email, email=email, password=password)
            user_obj.is_active = False
            user_obj.save()

            # Get role
            role_master = models.UserRoleMaster.objects.get(role_code=role)

            # Create UserProfile
            profile = models.UserProfile.objects.create(
                user=user_obj,
                name=name,
                email=email,
                role=role_master,
                mobile_number=mobile_number,
                dob=dob,
                university_id=university_id if university_id else None,
                is_approved=False
            )

            # Set role-specific fields
            if role == 'professor':
                profile.emp_id = request.POST.get('emp_id', '').strip()
            elif role == 'student':
                profile.enrollment_number = request.POST.get('enrollment_number', '').strip()
                
                course_id = request.POST.get('course', '').strip()
                if course_id:
                    profile.course_id = course_id
                
                session_id = request.POST.get('session', '').strip()
                if session_id:
                    profile.session_id = session_id

            profile.save()

            # Set preferred subjects
            if preferred_subjects:
                profile.preferred_subjects.set(preferred_subjects)

            return render(request, 'signup_success.html')
        except Exception as e:
            return render(request, 'signup.html', {
                'error': f'An error occurred: {str(e)}',
                'universities': models.UniversityMaster.objects.all(),
                'courses': models.CourseMaster.objects.all(),
                'sessions': models.SessionMaster.objects.all(),
                'subjects': models.SubjectMaster.objects.all()
            })

    # GET Request
    context = {
        'universities': models.UniversityMaster.objects.all(),
        'courses': models.CourseMaster.objects.all(),
        'sessions': models.SessionMaster.objects.all(),
        'subjects': models.SubjectMaster.objects.all()
    }
    return render(request, 'signup.html', context)

def logout_view(request):
    request.session.flush()
    return redirect('voice_tutor:index')

def get_filter_options_from_mapping(request):
    sort = request.GET.get('sort', 'desc')
    subject_id = request.GET.get('subject_id')
    topic_id = request.GET.get('topic_id')
    batch_id = request.GET.get('batch_id')
    professor_id = request.GET.get('professor_id')
    status = request.GET.get('status')

    allocations = models.ProfessorAllocation.objects.select_related('batch', 'subject', 'professor').all()
    
    professors = list({a.professor for a in allocations if a.professor})
    batches = list({a.batch for a in allocations if a.batch})
    subjects = list({a.subject for a in allocations if a.subject})
    
    professors.sort(key=lambda x: x.name)
    batches.sort(key=lambda x: x.name)
    subjects.sort(key=lambda x: x.name)
    
    subject_ids = [s.id for s in subjects]
    topics = models.TopicMaster.objects.filter(subject_id__in=subject_ids).order_by('name')

    return {
        'professors': professors,
        'batches': batches,
        'subjects': subjects,
        'topics': topics,
        'selected_subject': subject_id,
        'selected_topic': topic_id,
        'selected_batch': batch_id,
        'selected_professor': professor_id,
        'selected_sort': sort,
        'selected_status': status,
    }

@login_required_custom(role='student')
def student_dashboard(request):
    user = get_session_user(request)
    tab = request.GET.get('tab', 'dashboard')
    
    student_profile = models.UserProfile.objects.filter(user__username=user['id']).first()
    if not student_profile:
        student_profile = models.UserProfile.objects.filter(role__role_code='student').first()

    # Filter batchs and assignments
    student_batchs_qs = models.Batch.objects.filter(students=student_profile)
    student_batchs = []
    for c in student_batchs_qs:
        student_batchs.append({
            'id': c.code,
            'name': c.name,
            'description': c.description or '',
            'professorId': 'N/A',
            'studentIds': [s.user.username for s in c.students.all()],
            'assignmentIds': [],
            'createdAt': c.created_at.strftime('%d/%m/%Y')
        })
    sort = request.GET.get('sort', 'desc')
    subject_id = request.GET.get('subject_id')
    topic_id = request.GET.get('topic_id')
    batch_id = request.GET.get('batch_id')
    professor_id = request.GET.get('professor_id')
    status_filter = request.GET.get('status')

    queryset = models.Assignment.objects.all()

    if subject_id:
        queryset = queryset.filter(professor_allocation__subject__code=subject_id)
    if topic_id:
        queryset = queryset.filter(topic__code=topic_id)
    if batch_id:
        queryset = queryset.filter(professor_allocation__batch__code=batch_id)
    if professor_id:
        queryset = queryset.filter(professor_allocation__professor__user__username=professor_id)

    if sort == 'asc':
        queryset = queryset.order_by('created_at')
    else:
        queryset = queryset.order_by('-created_at')
        
    student_assignments = []
    for a in queryset:
        prog_obj = models.StudentTopicAssessmentReview.objects.filter(student=student_profile, assignment=a).first()
        current_status = prog_obj.status if prog_obj else 'not_started'
        
        if status_filter and status_filter != current_status:
            continue
            
        prog = None
        if prog_obj:
            prog = {
                'studentId': student_profile.user.username,
                'assignmentId': a.code,
                'questionsCompleted': prog_obj.questions_completed,
                'totalQuestions': prog_obj.total_questions,
                'timeSpent': prog_obj.time_spent,
                'hintsUsed': prog_obj.hints_used,
                'engagementScore': prog_obj.engagement_score,
                'status': prog_obj.status
            }
            
        questions_list = []
        for q in a.questions.all():
            options_list = [opt.option_text for opt in q.options.all().order_by('option_letter')]
            questions_list.append({
                'id': q.code,
                'content': q.content,
                'type': q.question_type,
                'options': options_list,
                'correctAnswer': q.correct_answer,
                'conceptTags': json.loads(q.concept_tags) if q.concept_tags else [],
                'difficulty': q.difficulty
            })

        student_assignments.append({
            'id': a.code,
            'title': a.title,
            'topic': a.topic.name,
            'description': a.description or '',
            'difficulty': a.difficulty,
            'questions': questions_list,
            'expectedDuration': a.expected_duration,
            'batchIds': [a.professor_allocation.batch.code] if a.professor_allocation else [],
            'createdAt': a.created_at,
            'progress': prog
        })

            
    # Calculate stats
    all_progress = models.StudentTopicAssessmentReview.objects.filter(student=student_profile)
    completed_count = all_progress.filter(status='completed').count()
    
    # Prepare Chart Data for Progress Tab
    chart_data = {
        'labels': [],
        'scores': []
    }
    completed_assessments = all_progress.filter(status='completed', score__isnull=False).order_by('updated_at')
    for assessment in completed_assessments:
        title = assessment.assignment.title if assessment.assignment else assessment.topic.name
        if len(title) > 20:
            title = title[:17] + "..."
        chart_data['labels'].append(title)
        chart_data['scores'].append(assessment.score)
        
    chart_data_json = json.dumps(chart_data)
    completed_count = all_progress.filter(status='completed').count()
    in_progress_count = all_progress.filter(status='in_progress').count()
    
    total_time_spent = sum([p.time_spent for p in all_progress])
    
    avg_engagement = 0
    if student_assignments:
        avg_engagement = round((completed_count / len(student_assignments)) * 100)
    
    voice_assignment_id = 'A001'
    continue_assignment_id = None
    if student_assignments:
        voice_assignment_id = student_assignments[0]['id']
        continue_assignment_id = student_assignments[0]['id']
        # Prioritize in-progress assignments, then completed
        for a in student_assignments:
            if a['progress'] and a['progress']['status'] == 'in_progress':
                continue_assignment_id = a['id']
                break
        else:
            for a in student_assignments:
                if a['progress'] and a['progress']['status'] == 'completed':
                    continue_assignment_id = a['id']
                    break
                
    continue_assignment = None
    if continue_assignment_id:
        for a in student_assignments:
            if a['id'] == continue_assignment_id:
                continue_assignment = a
                break

    # Filter options for the UI
    filter_options = get_filter_options_from_mapping(request)

    context = {
        'active_tab': tab,
        'first_name': user['name'].split(' ')[0] if user and user.get('name') else 'Student',
        'completed_count': completed_count,
        'in_progress_count': in_progress_count,
        'time_spent_mins': round(total_time_spent / 60),
        'engagement_score': avg_engagement,
        'subjects': get_serialized_subjects(),
        'student_assignments': student_assignments,
        'student_batchs': student_batchs,
        'voice_assignment_id': voice_assignment_id,
        'continue_assignment_id': continue_assignment_id,
        'continue_assignment': continue_assignment,
        'filters': filter_options,
        'chart_data_json': chart_data_json,
    }
    return render(request, 'student/dashboard.html', context)

@login_required_custom()
def take_assignment_view(request, assignment_id):
    assignment_obj = models.Assignment.objects.filter(code=assignment_id).first()
    if not assignment_obj:
        # Fallback for testing if ID doesn't match
        assignment_obj = models.Assignment.objects.first()
        
    assignment = None
    approved_visualizations = {}
    if assignment_obj:
        questions_list = []
        for q in assignment_obj.questions.all().order_by('order'):
            options_list = [
                {
                    'letter': opt.option_letter, 
                    'text': opt.option_text, 
                    'is_correct': (opt.option_letter.lower() == q.correct_answer.lower())
                } 
                for opt in q.options.all().order_by('option_letter')
            ]
            approved_visual = q.visualization_data if q.visualization_approved else None
            can_vis = isinstance(approved_visual, dict) and bool(approved_visual)
            if can_vis:
                approved_visualizations[q.code] = approved_visual
            questions_list.append({
                'id': q.code,
                'content': q.content,
                'type': q.question_type,
                'options': options_list,
                'correctAnswer': q.correct_answer,
                'difficulty': q.difficulty,
                'can_visualize': can_vis
            })
        assignment = {
            'id': assignment_obj.code,
            'title': assignment_obj.title,
            'topic': assignment_obj.topic.name,
            'description': assignment_obj.description or '',
            'difficulty': assignment_obj.difficulty,
            'questions': questions_list,
            'oralDefenseEnabled': bool(
                assignment_obj.oral_defense_enabled
                and isinstance(assignment_obj.oral_defense_config, dict)
            ) or assignment_obj.code == 'A_C2F6D9'
            or 'maxima and minima' in assignment_obj.title.lower()
            or 'profit maximization' in assignment_obj.title.lower(),
            'oralDefenseTitle': (
                assignment_obj.oral_defense_config.get('title')
                if isinstance(assignment_obj.oral_defense_config, dict)
                else 'AI Voice Oral Defense'
            ),
            'expectedDuration': assignment_obj.expected_duration,
            'createdAt': assignment_obj.created_at
        }
        
        # Set status to in_progress when the student starts the assignment
        user = get_session_user(request)
        time_spent = 0
        saved_answers = {}
        assignment_status = 'not_started'
        
        if user:
            student_profile = models.UserProfile.objects.filter(user__username=user['id']).first()
            if not student_profile:
                student_profile = models.UserProfile.objects.filter(role__role_code='student').first()
                
            if student_profile:
                prog_obj, created = models.StudentTopicAssessmentReview.objects.get_or_create(
                    student=student_profile,
                    assignment=assignment_obj,
                    topic=assignment_obj.topic,
                    defaults={
                        'status': 'in_progress',
                        'total_questions': assignment_obj.questions.count()
                    }
                )
                if not created and prog_obj.status == 'not_started':
                    prog_obj.status = 'in_progress'
                    prog_obj.save()
                time_spent = prog_obj.time_spent
                assignment_status = prog_obj.status
                if prog_obj.saved_answers:
                    saved_answers = prog_obj.saved_answers

    import json
    context = {
        'active_tab': 'assignments',
        'assignment': assignment,
        'time_spent': time_spent,
        'assignment_status': assignment_status,
        'saved_answers_json': json.dumps(saved_answers),
        'approved_visualizations': approved_visualizations,
    }
    return render(request, 'student/take_assignment.html', context)

@login_required_custom()
def voice_agent_view(request, assignment_id, question_id='Q001'):
    assignment_obj = models.Assignment.objects.filter(code=assignment_id).first()
    if not assignment_obj:
        assignment_obj = models.Assignment.objects.first()
        
    assignment = None
    if assignment_obj:
        questions_list = []
        for q in assignment_obj.questions.all().order_by('order'):
            options_list = [opt.option_text for opt in q.options.all().order_by('option_letter')]
            questions_list.append({
                'id': q.code,
                'content': q.content,
                'type': q.question_type,
                'options': options_list,
                'correctAnswer': q.correct_answer,
                'conceptTags': json.loads(q.concept_tags) if q.concept_tags else [],
                'difficulty': q.difficulty
            })
        assignment = {
            'id': assignment_obj.code,
            'title': assignment_obj.title,
            'topic': assignment_obj.topic.name,
            'description': assignment_obj.description or '',
            'difficulty': assignment_obj.difficulty,
            'questions': questions_list,
            'expectedDuration': assignment_obj.expected_duration,
            'createdAt': assignment_obj.created_at
        }
        
    question_query = models.AssignmentQuestion.objects.filter(code=question_id).first()
    if not question_query:
        question_query = models.AssignmentQuestion.objects.first()
        
    question = None
    if question_query:
        options_list = [opt.option_text for opt in question_query.options.all().order_by('option_letter')]
        question = {
            'id': question_query.code,
            'content': question_query.content,
            'type': question_query.question_type,
            'options': options_list,
            'correctAnswer': question_query.correct_answer,
            'conceptTags': json.loads(question_query.concept_tags) if question_query.concept_tags else [],
            'difficulty': question_query.difficulty
        }

    metrics_list = [
        'Understanding Level',
        'Hint Dependency',
        'Problem Solving Speed',
        'Confidence Growth',
    ]

    context = {
        'active_tab': 'assignments',
        'assignment': assignment,
        'question': question,
        'total_questions_count': len(assignment['questions']) if assignment else 4,
        'metrics_list': metrics_list
    }
    return render(request, 'student/voice_agent.html', context)

@login_required_custom()
def tutor_chat_view(request):
    user = get_session_user(request)
    mem0_user_id = user['email'] if user and user.get('email') else DEFAULT_MEM0_USER_ID
    
    assignment_id = request.GET.get('assignment_id')
    question_id = request.GET.get('question_id')
    student_answer = request.GET.get('student_answer')
    is_correct = request.GET.get('is_correct')
    
    question_context = None
    question_text = None
    student_answer_text = None
    if question_id:
        qs = models.AssignmentQuestion.objects.filter(code=question_id)
        if assignment_id:
            qs = qs.filter(assignment__code=assignment_id)
        question_obj = qs.first()
        if question_obj:
            options = question_obj.options.all().order_by('option_letter')
            options_text = ", ".join([f"{opt.option_letter}: {opt.option_text}" for opt in options])
            question_text = question_obj.content
            
            question_context = f"The student is asking for help with the following question: '{question_obj.content}'. The options are: {options_text}. The correct answer is {question_obj.correct_answer}."
            
            if student_answer:
                # Find the text of the selected option
                selected_opt = options.filter(option_letter=student_answer).first()
                if selected_opt:
                    student_answer_text = selected_opt.option_text
                
                status_text = "correct" if is_correct == 'true' else "incorrect"
                question_context += f" The student selected option {student_answer} ({student_answer_text}), which is {status_text}."
                
            question_context += " Provide hints and guidance to help them understand the concept, but do not give away the exact answer immediately."

    return render(request, 'student/tutor_chat.html', {
        'active_tab': 'tutor_chat',
        'mem0_user_id': mem0_user_id,
        'question_text': question_text,
        'question_context': question_context,
        'student_answer': student_answer,
        'student_answer_text': student_answer_text,
        'is_correct': is_correct,
    })

@login_required_custom()
def openai_voice_tutor_view(request):
    user = get_session_user(request)
    mem0_user_id = user['email'] if user and user.get('email') else DEFAULT_MEM0_USER_ID
    
    context = {
        'active_tab': 'dashboard',
        'mem0_user_id': mem0_user_id,
        'initial_topic': ''
    }
    return render(request, 'student/openai_voice_tutor.html', context)

@login_required_custom()
def topic_page_view(request, subject_id, topic_id):
    user = get_session_user(request)
    
    subject_obj = models.SubjectMaster.objects.filter(code=subject_id).first()
    topic_obj = models.TopicMaster.objects.filter(code=topic_id).first()
    
    topic = None
    if topic_obj:
        learning_objs = []
        if topic_obj.learning_objectives:
            try:
                learning_objs = json.loads(topic_obj.learning_objectives)
            except Exception:
                pass
        
        videos_list = []
        for v in topic_obj.tutorial_videos.all():
            videos_list.append({
                'id': v.code or f"VID{v.id}",
                'title': v.title,
                'src': v.src,
                'description': v.description or ''
            })
        
        guided_ids = [a.code for a in topic_obj.assignments.exclude(code__startswith='A_PRACTICE')]
        
        questions_list = []
        for a in topic_obj.assignments.all():
            for q in a.questions.all().order_by('order'):
                options_list = [opt.option_text for opt in q.options.all().order_by('option_letter')]
                questions_list.append({
                    'id': q.code,
                    'content': q.content,
                    'type': q.question_type,
                    'options': options_list,
                    'correctAnswer': q.correct_answer,
                    'conceptTags': json.loads(q.concept_tags) if q.concept_tags else [],
                    'difficulty': q.difficulty
                })
                
        topic = {
            'id': topic_obj.code,
            'subjectId': subject_obj.code if subject_obj else '',
            'name': topic_obj.name,
            'description': topic_obj.description or '',
            'learningObjectives': learning_objs,
            'videos': videos_list,
            'guidedAssignmentIds': guided_ids,
            'assignmentQuestions': questions_list
        }
        
    subject = None
    if subject_obj:
        subject = {
            'id': subject_obj.code,
            'name': subject_obj.name,
            'description': subject_obj.description or ''
        }
    
    context = {
        'active_tab': 'dashboard',
        'topic': topic,
        'subject': subject,
        'is_professor': (user.get('role') == 'professor')
    }
    return render(request, 'topic.html', context)

@login_required_custom(role='professor')
def professor_dashboard(request):
    user = get_session_user(request)
    prof_profile = models.UserProfile.objects.filter(user__username=user['id']).first()
    if not prof_profile:
        prof_profile = models.UserProfile.objects.filter(role__role_code='professor').first()

    allocations = models.ProfessorAllocation.objects.filter(professor=prof_profile).select_related('batch')
    batchs_qs = list({a.batch for a in allocations if a.batch})
    
    total_students = sum([c.students.count() for c in batchs_qs])
    
    assignment_counts_by_batch = {
        item['professor_allocation__batch_id']: item['assignment_count']
        for item in models.Assignment.objects.filter(
            professor_allocation__in=allocations
        ).values('professor_allocation__batch_id').annotate(
            assignment_count=Count('id')
        )
    }

    batchs_list = []
    for c in batchs_qs:
        c_copy = {
            'id': c.code,
            'name': c.name,
            'description': c.description or '',
            'professorId': 'N/A',
            'studentIds': [s.user.username for s in c.students.all()],
            'assignmentCount': assignment_counts_by_batch.get(c.id, 0),
            'createdAt': c.created_at
        }
        students = []
        for s in c.students.all():
            students.append({
                'id': s.user.username,
                'name': s.name,
                'email': s.email,
                'role': s.role.role_code,
                'avatar': s.avatar,
                'createdAt': s.created_at
            })
            
        c_copy['students'] = students
        
        first_names = [s['name'].split(' ')[0] for s in students[:2]]
        names_str = ", ".join(first_names)
        if len(students) > 2:
            names_str += f" & {len(students) - 2} more"
        c_copy['students_display_str'] = names_str
        c_copy['avatar_students'] = students[:4]
        c_copy['remaining_students_count'] = max(0, len(students) - 4)
        batchs_list.append(c_copy)
        
    high_count = models.StudentTopicAssessmentReview.objects.filter(score__gte=80).count()
    med_count = models.StudentTopicAssessmentReview.objects.filter(score__lt=80, score__gte=50).count()
    low_count = models.StudentTopicAssessmentReview.objects.filter(score__lt=50).count()
    
    if high_count == 0 and med_count == 0 and low_count == 0:
        high_count, med_count, low_count = 1, 1, 1

    analytics = {
        'batchId': 'C101',
        'overallEngagement': 74,
        'commonWeakConcepts': ['Sample Space Identification', 'Counting Favorable Outcomes'],
        'performanceDistribution': {
            'high': high_count,
            'medium': med_count,
            'low': low_count,
        },
        'outliers': ['student_003'],
    }
    
    recent_assignments = []
    assignments_qs = models.Assignment.objects.annotate(
        question_count=Count('questions')
    ).order_by('-created_at')[:2]
    for a in assignments_qs:
        recent_assignments.append({
            'id': a.code,
            'title': a.title,
            'topic': a.topic.name,
            'description': a.description or '',
            'difficulty': a.difficulty,
            'expectedDuration': a.expected_duration,
            'questionCount': a.question_count,
            'createdAt': a.created_at
        })

    context = {
        'active_tab': 'dashboard',
        'total_students': total_students,
        'active_assignments_count': models.Assignment.objects.all().count(),
        'batch_analytics': analytics,
        'batchs': batchs_list,
        'subjects': get_serialized_subjects(),
        'recent_assignments': recent_assignments
    }
    return render(request, 'professor/dashboard.html', context)

@login_required_custom(role='professor')
def professor_batchs(request):
    user = get_session_user(request)
    prof_profile = models.UserProfile.objects.filter(user__username=user['id']).first()
    if not prof_profile:
        prof_profile = models.UserProfile.objects.filter(role__role_code='professor').first()

    allocations = models.ProfessorAllocation.objects.filter(professor=prof_profile).select_related('batch')
    
    # Extract unique filter options from mapped batches
    all_mapped_batches = {a.batch for a in allocations if a.batch}
    filter_universities = list({b.university for b in all_mapped_batches if b.university})
    filter_sessions = list({b.session for b in all_mapped_batches if b.session})
    filter_courses = list({b.course for b in all_mapped_batches if b.course})
    
    filter_universities.sort(key=lambda x: x.name)
    filter_sessions.sort(key=lambda x: x.name)
    filter_courses.sort(key=lambda x: x.name)

    # Apply filters
    batch_id = request.GET.get('batch_id')
    university_id = request.GET.get('university_id')
    session_id = request.GET.get('session_id')
    course_id = request.GET.get('course_id')

    batchs_qs = list(all_mapped_batches)
    if batch_id:
        batchs_qs = [b for b in batchs_qs if b.code == batch_id]
    if university_id:
        batchs_qs = [b for b in batchs_qs if b.university and str(b.university.id) == university_id]
    if session_id:
        batchs_qs = [b for b in batchs_qs if b.session and str(b.session.id) == session_id]
    if course_id:
        batchs_qs = [b for b in batchs_qs if b.course and str(b.course.id) == course_id]

    batchs_list = []
    for c in batchs_qs:
        c_copy = {
            'id': c.code,
            'name': c.name,
            'description': c.description or '',
            'university': c.university.name if c.university else 'N/A',
            'session': c.session.name if c.session else 'N/A',
            'course': c.course.name if c.course else 'N/A',
            'studentIds': [s.user.username for s in c.students.all()],
            'assignmentIds': [],
            'createdAt': c.created_at.strftime('%d/%m/%Y')
        }
        students = []
        for s in c.students.all():
            students.append({
                'id': s.user.username,
                'name': s.name,
                'email': s.email,
                'role': s.role.role_code,
                'avatar': s.avatar,
                'createdAt': s.created_at
            })
        c_copy['students'] = students
        batchs_list.append(c_copy)

    context = {
        'active_tab': 'batchs',
        'batchs': batchs_list,
        'filters': {
            'universities': filter_universities,
            'sessions': filter_sessions,
            'courses': filter_courses,
            'selected_university': university_id,
            'selected_session': session_id,
            'selected_course': course_id,
        }
    }
    return render(request, 'professor/batches.html', context)

@login_required_custom(role='professor')
def professor_batch_detail(request, batch_id):
    user = get_session_user(request)
    prof_profile = models.UserProfile.objects.filter(user__username=user['id']).first()
    if not prof_profile:
        prof_profile = models.UserProfile.objects.filter(role__role_code='professor').first()

    # Validate that this batch is mapped to the professor
    allocations = models.ProfessorAllocation.objects.filter(professor=prof_profile, batch__code=batch_id).select_related('batch', 'subject', 'subject__course', 'subject__session')
    if not allocations.exists():
        batch_obj = models.Batch.objects.filter(code=batch_id).first()
        if not batch_obj:
            return redirect('voice_tutor:professor_batchs')
        batch = batch_obj
    else:
        batch = allocations.first().batch
    
    # Get subjects assigned to this batch and professor
    subjects_set = {a.subject for a in allocations if a.subject}
    if not subjects_set and batch.course:
        subjects_set = set(batch.course.subjects.all())

    subjects_list = []
    for s in sorted(list(subjects_set), key=lambda x: x.name):
        subjects_list.append({
            'id': s.id,
            'code': s.code,
            'name': s.name,
            'course': s.course.name if s.course else (batch.course.name if batch.course else 'N/A'),
            'session': s.session.name if s.session else (batch.session.name if batch.session else 'N/A'),
            'semester': s.semester_or_year,
            'description': s.description or '',
            'topics_count': s.topics.count(),
        })
        
    assignments = []
    # Assignments explicitly mapped for this specific batch
    batch_assignments = models.Assignment.objects.filter(professor_allocation__in=allocations)
    for a in batch_assignments:
        assignments.append({
            'id': a.code,
            'title': a.title,
            'topic': a.topic.name if a.topic else 'N/A',
            'createdAt': a.created_at.strftime('%d/%m/%Y')
        })

    context = {
        'active_tab': 'batchs',
        'batch': batch,
        'subjects': subjects_list,
        'assignments': assignments,
    }
    return render(request, 'professor/batch_detail.html', context)

@login_required_custom(role='professor')
def professor_subjects(request):
    user = get_session_user(request)
    prof_profile = models.UserProfile.objects.filter(user__username=user['id']).first()
    if not prof_profile:
        prof_profile = models.UserProfile.objects.filter(role__role_code='professor').first()

    # Get all unique subjects mapped to this professor
    allocations = models.ProfessorAllocation.objects.filter(professor=prof_profile).select_related('subject', 'subject__course', 'subject__session')
    
    subjects_set = {a.subject for a in allocations if a.subject}
    subjects_list = []
    
    for s in subjects_set:
        subjects_list.append({
            'code': s.code,
            'name': s.name,
            'course': s.course.name if s.course else 'N/A',
            'session': s.session.name if s.session else 'N/A',
            'semester': s.semester_or_year,
            'description': s.description or ''
        })

    # Sort alphabetically by name
    subjects_list.sort(key=lambda x: x['name'])

    context = {
        'active_tab': 'subjects',
        'subjects': subjects_list
    }
    return render(request, 'professor/subjects.html', context)

@login_required_custom(role='professor')
def professor_topics(request):
    user = get_session_user(request)
    prof_profile = models.UserProfile.objects.filter(user__username=user['id']).first()
    if not prof_profile:
        prof_profile = models.UserProfile.objects.filter(role__role_code='professor').first()

    # Get mapped subjects
    allocations = models.ProfessorAllocation.objects.filter(professor=prof_profile).select_related('subject')
    subjects_set = {a.subject for a in allocations if a.subject}
    subject_ids = [s.id for s in subjects_set]

    if request.method == 'POST':
        # Create a new Topic
        name = request.POST.get('name')
        code = request.POST.get('code')
        subject_id = request.POST.get('subject_id')
        description = request.POST.get('description')
        learning_objectives = request.POST.get('learning_objectives') # comma separated or newlines

        if subject_id and int(subject_id) in subject_ids:
            subject = models.SubjectMaster.objects.get(id=subject_id)
            models.TopicMaster.objects.create(
                name=name,
                code=code,
                subject=subject,
                description=description,
                learning_objectives=learning_objectives
            )
        return redirect('voice_tutor:professor_topics')

    # GET: List topics
    subject_id_filter = request.GET.get('subject_id')
    batch_id_filter = request.GET.get('batch_id')

    topics = models.TopicMaster.objects.filter(subject_id__in=subject_ids).select_related('subject')
    
    selected_subject = None
    if subject_id_filter:
        try:
            if subject_id_filter.isdigit():
                topics = topics.filter(subject_id=int(subject_id_filter))
                selected_subject = models.SubjectMaster.objects.filter(id=int(subject_id_filter)).first()
            else:
                topics = topics.filter(subject__code=subject_id_filter)
                selected_subject = models.SubjectMaster.objects.filter(code=subject_id_filter).first()
        except Exception:
            pass

    selected_batch = None
    if batch_id_filter:
        selected_batch = models.Batch.objects.filter(code=batch_id_filter).first()

    context = {
        'active_tab': 'topics',
        'topics': topics,
        'subjects': sorted(list(subjects_set), key=lambda x: x.name),
        'selected_subject': selected_subject,
        'selected_batch': selected_batch,
        'filter_subject_id': subject_id_filter or '',
        'filter_batch_id': batch_id_filter or '',
    }
    return render(request, 'professor/topics.html', context)

@login_required_custom(role='professor')
def professor_topic_update(request, topic_id):
    if request.method == 'POST':
        user = get_session_user(request)
        prof_profile = models.UserProfile.objects.filter(user__username=user['id']).first()
        if not prof_profile:
            prof_profile = models.UserProfile.objects.filter(role__role_code='professor').first()

        allocations = models.ProfessorAllocation.objects.filter(professor=prof_profile).select_related('subject')
        subject_ids = [a.subject.id for a in allocations if a.subject]

        topic = models.TopicMaster.objects.filter(id=topic_id).first()
        if not topic:
            return redirect('voice_tutor:professor_topics')
        
        # Verify the topic belongs to a mapped subject
        if not subject_ids or topic.subject.id in subject_ids:
            new_subject_id = request.POST.get('subject_id')
            # Verify the new subject is also mapped
            if new_subject_id and (not subject_ids or int(new_subject_id) in subject_ids):
                topic.name = request.POST.get('name')
                topic.code = request.POST.get('code')
                topic.description = request.POST.get('description')
                topic.learning_objectives = request.POST.get('learning_objectives')
                topic.subject_id = int(new_subject_id)
                topic.save()
    
    return redirect('voice_tutor:professor_topics')

@login_required_custom(role='professor')
def professor_topic_delete(request, topic_id):
    if request.method == 'POST':
        user = get_session_user(request)
        prof_profile = models.UserProfile.objects.filter(user__username=user['id']).first()
        if not prof_profile:
            prof_profile = models.UserProfile.objects.filter(role__role_code='professor').first()

        allocations = models.ProfessorAllocation.objects.filter(professor=prof_profile).select_related('subject')
        subject_ids = [a.subject.id for a in allocations if a.subject]

        topic = models.TopicMaster.objects.filter(id=topic_id).first()
        if not topic:
            # Topic is already deleted or not found; safely return to topics list
            return redirect('voice_tutor:professor_topics')
        
        # Verify the topic belongs to a mapped subject
        if not subject_ids or topic.subject.id in subject_ids:
            topic.delete()

    return redirect('voice_tutor:professor_topics')

@login_required_custom(role='professor')
def professor_topic_preview(request, topic_id):
    user = get_session_user(request)
    prof_profile = models.UserProfile.objects.filter(user__username=user['id']).first()
    if not prof_profile:
        prof_profile = models.UserProfile.objects.filter(role__role_code='professor').first()

    topic = models.TopicMaster.objects.filter(id=topic_id).select_related('subject').first()
    if not topic:
        return redirect('voice_tutor:professor_topics')

    # Fetch latest summary notes for this topic
    note = models.SummaryNotes.objects.filter(topic=topic).order_by('-created_at').first()

    # Fetch assignment and its questions for this topic
    assignment = models.Assignment.objects.filter(topic=topic).order_by('-created_at').first()
    questions_data = []
    if assignment:
        for q in assignment.questions.all().order_by('order'):
            options = []
            for opt in q.options.all().order_by('option_letter'):
                options.append({
                    'letter': opt.option_letter.lower(),
                    'text': opt.option_text,
                    'is_correct': (opt.option_letter.lower() == q.correct_answer.lower())
                })
            questions_data.append({
                'id': q.code,
                'content': q.content,
                'type': q.question_type,
                'difficulty': q.difficulty,
                'correct_answer': q.correct_answer.lower(),
                'concept_tags': json.loads(q.concept_tags) if q.concept_tags else [],
                'options': options
            })

    game_config = (
        assignment.oral_defense_config
        if assignment
        and assignment.oral_defense_enabled
        and isinstance(assignment.oral_defense_config, dict)
        else None
    )
    game_levels = game_config.get('levels', []) if game_config else []
    game_enabled = bool(game_config and game_levels)

    # Find associated batch if available
    batch_name = None
    if assignment and assignment.professor_allocation and assignment.professor_allocation.batch:
        batch_name = assignment.professor_allocation.batch.name
    elif prof_profile:
        alloc = models.ProfessorAllocation.objects.filter(professor=prof_profile, subject=topic.subject).select_related('batch').first()
        if alloc and alloc.batch:
            batch_name = alloc.batch.name

    context = {
        'topic': topic,
        'subject': topic.subject,
        'note': note,
        'assignment': assignment,
        'questions_json': json.dumps(questions_data),
        'question_count': len(questions_data),
        'batch_name': batch_name or 'General',
        'game_enabled': game_enabled,
        'game_title': (
            game_config.get('title')
            if game_enabled
            else None
        ),
        'game_levels': game_levels,
    }
    return render(request, 'professor/topic_preview.html', context)

@login_required_custom(role='professor')
def professor_assignments(request):
    user = get_session_user(request)
    prof_profile = models.UserProfile.objects.filter(user__username=user['id']).first()
    if not prof_profile:
        prof_profile = models.UserProfile.objects.filter(role__role_code='professor').first()

    sort = request.GET.get('sort', 'desc')
    subject_id = request.GET.get('subject_id')
    topic_id = request.GET.get('topic_id')
    batch_id = request.GET.get('batch_id')
    
    # Restrict completely to the current professor
    queryset = models.Assignment.objects.filter(professor_allocation__professor=prof_profile)

    if subject_id:
        queryset = queryset.filter(professor_allocation__subject__code=subject_id)
    if topic_id:
        queryset = queryset.filter(topic__code=topic_id)
    if batch_id:
        queryset = queryset.filter(professor_allocation__batch__code=batch_id)

    if sort == 'asc':
        queryset = queryset.order_by('created_at')
    else:
        queryset = queryset.order_by('-created_at')

    assignments_list = []
    for a in queryset:
        a_copy = {
            'id': a.code,
            'title': a.title,
            'subject': a.topic.subject.name if a.topic and a.topic.subject else 'Unknown Subject',
            'topic': a.topic.name if a.topic else 'Unknown Topic',
            'batch': 'No batch',
            'description': a.description or '',
            'difficulty': a.difficulty,
            'expectedDuration': a.expected_duration,
            'createdAt': a.created_at,
            'createdBy': a.created_by.get_full_name() if a.created_by else (a.created_by.username if a.created_by else 'System'),
            'has_pdf': bool(a.source_pdf_path),
            'questions': [{'id': q.code} for q in a.questions.all()]
        }
        progress_qs = models.StudentTopicAssessmentReview.objects.filter(assignment=a)
        completed_qs = progress_qs.filter(status='completed')
        avg_engagement = round(sum([p.engagement_score for p in progress_qs]) / max(progress_qs.count(), 1)) if progress_qs.exists() else 0
        
        a_copy['progressCount'] = progress_qs.count()
        a_copy['completedCount'] = completed_qs.count()
        a_copy['avgEngagement'] = avg_engagement
        assignments_list.append(a_copy)

    active_jobs = []
    from config import get_settings
    import json
    import time
    settings_obj = get_settings()
    jobs_dir = settings_obj.UPLOADS_DIR / "jobs"
    if jobs_dir.exists():
        current_time = time.time()
        for job_file in jobs_dir.glob("*.json"):
            try:
                if current_time - job_file.stat().st_mtime > 600:
                    job_file.unlink()
                    continue
                    
                with open(job_file, "r") as jf:
                    data = json.load(jf)
                # If it's not complete or failed, add it.
                if data.get("progress", 100) < 100:
                    data["job_id"] = job_file.stem
                    active_jobs.append(data)
                else:
                    job_file.unlink()
            except Exception:
                pass

    batches = []
    batch_subjects = {}
    subject_topics = {}

    if prof_profile:
        allocations = models.ProfessorAllocation.objects.filter(professor=prof_profile).select_related('batch', 'subject')
        
        # Map Batch -> Subjects
        for alloc in allocations:
            b_code = alloc.batch.code
            if b_code not in batch_subjects:
                batch_subjects[b_code] = []
                batches.append(alloc.batch)
            
            # Check for duplicates in subject list
            if not any(s['code'] == alloc.subject.code for s in batch_subjects[b_code]):
                batch_subjects[b_code].append({
                    'code': alloc.subject.code,
                    'name': alloc.subject.name
                })
                
        # Map Subject -> Topics (only for subjects in allocations)
        subject_ids = [alloc.subject.id for alloc in allocations]
        topics = models.TopicMaster.objects.filter(subject_id__in=subject_ids).select_related('subject')
        for t in topics:
            s_code = t.subject.code
            if s_code not in subject_topics:
                subject_topics[s_code] = []
            subject_topics[s_code].append({
                'code': t.code,
                'name': t.name
            })
        
    # Filter options for the UI
    filter_options = get_filter_options_from_mapping(request)

    context = {
        'active_tab': 'assignments',
        'assignments': assignments_list,
        'active_jobs_json': json.dumps(active_jobs),
        'batches': batches,
        'batch_subjects_json': json.dumps(batch_subjects),
        'subject_topics_json': json.dumps(subject_topics),
        'filters': filter_options,
    }
    return render(request, 'professor/assignments.html', context)

@login_required_custom(role='professor')
def professor_assignment_detail(request, assignment_id):
    assignment_obj = models.Assignment.objects.filter(code=assignment_id).first()
    if not assignment_obj:
        return redirect('voice_tutor:professor_assignments')

    # Fetch questions
    questions_list = []
    for q in assignment_obj.questions.all().order_by('order'):
        options_list = [
            {
                'letter': opt.option_letter,
                'text': opt.option_text,
                'is_correct': (opt.option_letter.lower() == q.correct_answer.lower())
            }
            for opt in q.options.all().order_by('option_letter')
        ]
        questions_list.append({
            'id': q.code,
            'content': q.content,
            'type': q.question_type,
            'options': options_list,
            'correctAnswer': q.correct_answer,
            'difficulty': q.difficulty,
            'conceptTags': json.loads(q.concept_tags) if q.concept_tags else []
        })

    # Fetch student progress
    progress_qs = models.StudentTopicAssessmentReview.objects.filter(assignment=assignment_obj)
    students_progress = []
    for prog in progress_qs:
        pct = round((prog.questions_completed / max(prog.total_questions, 1)) * 100)
        students_progress.append({
            'student_name': prog.student.name,
            'student_avatar': prog.student.avatar,
            'pct': pct,
            'engagement': prog.engagement_score,
            'hints_used': prog.hints_used,
            'status': prog.status,
            'time_spent': prog.time_spent
        })

    assignment_data = {
        'id': assignment_obj.code,
        'title': assignment_obj.title,
        'subject': assignment_obj.topic.subject.name if assignment_obj.topic and assignment_obj.topic.subject else 'Unknown Subject',
        'topic': assignment_obj.topic.name if assignment_obj.topic else 'Unknown Topic',
        'batch': 'No batch',
        'description': assignment_obj.description or '',
        'difficulty': assignment_obj.difficulty,
        'expectedDuration': assignment_obj.expected_duration,
        'createdAt': assignment_obj.created_at,
        'createdBy': assignment_obj.created_by.get_full_name() if assignment_obj.created_by else (assignment_obj.created_by.username if assignment_obj.created_by else 'System'),
        'has_pdf': bool(assignment_obj.source_pdf_path),
        'questions': questions_list,
        'students_progress': students_progress,
        'progressCount': progress_qs.count(),
        'completedCount': progress_qs.filter(status='completed').count(),
        'avgEngagement': round(sum([p.engagement_score for p in progress_qs]) / max(progress_qs.count(), 1)) if progress_qs.exists() else 0
    }

    context = {
        'active_tab': 'assignments',
        'assignment': assignment_data
    }
    return render(request, 'professor/assignment_detail.html', context)


@login_required_custom(role='professor')
def professor_assignment_delete(request, assignment_id):
    if request.method != 'POST':
        return redirect('voice_tutor:professor_assignments')

    assignment_obj = models.Assignment.objects.filter(code=assignment_id).first()
    if not assignment_obj:
        return redirect('voice_tutor:professor_assignments')

    # Delete the associated vectors from Qdrant if a PDF was uploaded
    if assignment_obj.source_pdf_path:
        from pathlib import Path
        from app.services.vector_store import delete_documents_by_source
        source_filename = Path(assignment_obj.source_pdf_path).name
        delete_documents_by_source(source_filename)
        
    # Delete the associated study notes if they exist
    if assignment_obj.topic:
        models.SummaryNotes.objects.filter(topic=assignment_obj.topic).delete()
        
    # Delete the assignment (cascades to questions and options)
    assignment_obj.delete()
    
    return redirect('voice_tutor:professor_assignments')


@login_required_custom()
def serve_assignment_pdf(request, assignment_id):
    from django.http import FileResponse, Http404
    import os
    assignment_obj = models.Assignment.objects.filter(code=assignment_id).first()
    if not assignment_obj or not assignment_obj.source_pdf_path:
        raise Http404("PDF not found for this assignment")
    
    file_path = assignment_obj.source_pdf_path
    if not os.path.exists(file_path):
        raise Http404("PDF file missing on server")
        
    return FileResponse(open(file_path, 'rb'), content_type='application/pdf')

@login_required_custom(role='professor')
def professor_analytics(request):
    high_count = models.StudentTopicAssessmentReview.objects.filter(score__gte=80).count()
    med_count = models.StudentTopicAssessmentReview.objects.filter(score__lt=80, score__gte=50).count()
    low_count = models.StudentTopicAssessmentReview.objects.filter(score__lt=50).count()
    
    if high_count == 0 and med_count == 0 and low_count == 0:
        high_count, med_count, low_count = 1, 1, 1

    batch_analytics = {
        'batchId': 'C101',
        'overallEngagement': 74,
        'commonWeakConcepts': ['Sample Space Identification', 'Counting Favorable Outcomes'],
        'performanceDistribution': {
            'high': high_count,
            'medium': med_count,
            'low': low_count,
        },
        'outliers': ['student_003'],
    }

    assignment_analytics = {
        'assignmentId': 'A001',
        'completionRate': 67,
        'averageTime': 25,
        'engagementScore': 74,
        'questionDifficulty': [
            {'questionId': 'Q001', 'score': 3.2},
            {'questionId': 'Q002', 'score': 4.5},
            {'questionId': 'Q003', 'score': 2.8},
            {'questionId': 'Q004', 'score': 1.5},
        ],
        'dropOffPoints': [
            {'questionId': 'Q002', 'count': 5},
            {'questionId': 'Q003', 'count': 2},
        ],
    }
    
    performance_distribution_list = []
    total = sum(batch_analytics['performanceDistribution'].values())
    for level, count in batch_analytics['performanceDistribution'].items():
        performance_distribution_list.append({
            'level': level,
            'count': count,
            'pct': round((count / max(total, 1)) * 100)
        })

    student_progress_overview = []
    for prog in models.StudentTopicAssessmentReview.objects.all():
        pct = round((prog.questions_completed / max(prog.total_questions, 1)) * 100)
        student_progress_overview.append({
            'student_name': prog.student.name,
            'assignment_title': prog.assignment.title if prog.assignment else 'Topic Practice',
            'pct': pct,
            'engagement': prog.engagement_score,
            'hints_used': prog.hints_used,
            'status': prog.status
        })

    context = {
        'active_tab': 'analytics',
        'batch_analytics': batch_analytics,
        'assignment_analytics': assignment_analytics,
        'performance_distribution_list': performance_distribution_list,
        'student_progress_overview': student_progress_overview
    }
    return render(request, 'professor/analytics.html', context)

@login_required_custom(role='professor')
def professor_messages(request):
    students_profiles = models.UserProfile.objects.filter(role__role_code='student')
    students = []
    for s in students_profiles:
        students.append({
            'id': s.user.username,
            'name': s.name,
            'email': s.email,
            'role': s.role.role_code,
            'avatar': s.avatar,
            'createdAt': s.created_at
        })
        
    context = {
        'active_tab': 'messages',
        'students': students
    }
    return render(request, 'professor/messages.html', context)
@login_required_custom(role='student')
def student_messages(request):
    professors_profiles = models.UserProfile.objects.filter(role__role_code='professor')
    students = []
    for p in professors_profiles:
        students.append({
            'id': p.user.username,
            'name': p.name,
            'email': p.email,
            'role': p.role.role_code,
            'avatar': p.avatar,
            'createdAt': p.created_at
        })
        
    context = {
        'active_tab': 'messages',
        'students': students  # using 'students' key to reuse template logic
    }
    return render(request, 'student/messages.html', context)

# --- Backend API Views (Asynchronous & CSRF-Exempt) ---

async def run_async_generator(async_gen):
    async for chunk in async_gen:
        yield chunk

@csrf_exempt
async def api_start_call(request):
    if request.method != 'POST':
        return JsonResponse({'detail': 'Method not allowed'}, status=405)
    
    try:
        body = json.loads(request.body)
    except Exception:
        return JsonResponse({'detail': 'Invalid JSON body'}, status=400)
    
    user_id = body.get('user_id')
    lead_id = body.get('lead_id')
    user_name = body.get('user_name')
    user_email = body.get('user_email')
    
    service = get_elevenlabs_service()
    uid = user_id.strip() if user_id and user_id.strip() else DEFAULT_MEM0_USER_ID
    
    memories = None
    try:
        mem0 = get_mem0_service()
        query_parts = ["weak topics", lead_id, user_name, user_email]
        query = " ".join([part.strip() for part in query_parts if isinstance(part, str) and part.strip()])
        memories = await mem0.retrieve_memories(query, uid)
    except Exception as exc:
        pass
        
    try:
        result = await service.start_conversation(
            user_id=uid,
            lead_id=lead_id,
            user_name=user_name,
            user_email=user_email,
            memories=memories,
        )
        try:
            db = get_database()
            db.execute(
                """
                INSERT INTO voice_calls (conversation_id, lead_id, user_id, started_at, status)
                VALUES (?, ?, ?, ?, ?)
                """,
                (result["conversation_id"], lead_id, uid, datetime.datetime.utcnow(), "active"),
            )
            db.commit()
        except Exception:
            pass
            
        return JsonResponse(result)
    except Exception as e:
        return JsonResponse({'detail': str(e)}, status=500)

@csrf_exempt
async def api_get_call_status(request, conversation_id):
    service = get_elevenlabs_service()
    try:
        result = await service.get_conversation_status(conversation_id)
        if "error" in result:
            return JsonResponse({'detail': result["error"]}, status=404)
        return JsonResponse(result)
    except Exception as e:
        return JsonResponse({'detail': str(e)}, status=500)

@csrf_exempt
async def api_end_call(request):
    if request.method != 'POST':
        return JsonResponse({'detail': 'Method not allowed'}, status=405)
        
    try:
        body = json.loads(request.body)
    except Exception:
        return JsonResponse({'detail': 'Invalid JSON body'}, status=400)
        
    conversation_id = body.get('conversation_id')
    if not conversation_id:
        return JsonResponse({'detail': 'conversation_id is required'}, status=400)
        
    service = get_elevenlabs_service()
    try:
        result = await service.end_conversation(conversation_id)
        if "error" in result:
            return JsonResponse({'detail': result["error"]}, status=404)
            
        try:
            user_id = None
            lead_id = None
            try:
                db = get_database()
                row = db.execute(
                    "SELECT lead_id, user_id FROM voice_calls WHERE conversation_id=?",
                    (conversation_id,),
                ).fetchone()
                if row:
                    lead_id = row[0]
                    user_id = row[1]
            except Exception:
                pass

            if not user_id:
                conv = service.get_conversation(conversation_id)
                if conv and isinstance(conv.get("user_id"), str):
                    user_id = conv["user_id"]
                if conv and isinstance(conv.get("lead_id"), str):
                    lead_id = conv["lead_id"]

            transcript = result.get("transcript")
            if user_id and isinstance(transcript, list) and transcript:
                lines = []
                for item in transcript:
                    if not isinstance(item, dict):
                        continue
                    role = item.get("role") or item.get("source") or item.get("speaker") or "agent"
                    text = (
                        item.get("message")
                        or item.get("text")
                        or item.get("content")
                        or item.get("transcript")
                    )
                    if not isinstance(text, str) or not text.strip():
                        continue
                    prefix = "User" if str(role).lower() in {"user", "human"} else "Tutor"
                    lines.append(f"{prefix}: {text.strip()}")

                if lines:
                    transcript_text = "\n".join(lines)
                    transcript_text = transcript_text[:6000]
                    summary_header = f"Voice tutor session (conversation_id={conversation_id}"
                    if lead_id:
                        summary_header += f", lead_id={lead_id}"
                    summary_header += ")"
                    mem0 = get_mem0_service()
                    await mem0.add_memory(f"{summary_header}\n{transcript_text}", user_id)
        except Exception:
            pass
            
        try:
            db = get_database()
            db.execute(
                "UPDATE voice_calls SET status='ended', ended_at=? WHERE conversation_id=?",
                (datetime.datetime.utcnow(), conversation_id),
            )
            db.commit()
        except Exception:
            pass
            
        return JsonResponse(result)
    except Exception as e:
        return JsonResponse({'detail': str(e)}, status=500)

@csrf_exempt
async def api_get_call_history(request):
    user_id = request.GET.get('user_id')
    lead_id = request.GET.get('lead_id')
    
    service = get_elevenlabs_service()
    try:
        conversations = service.list_conversations(user_id=user_id)
        if lead_id:
            conversations = [conv for conv in conversations if conv.get("lead_id") == lead_id]
            
        try:
            db = get_database()
            if user_id:
                rows = db.execute(
                    "SELECT * FROM voice_calls WHERE user_id=? ORDER BY started_at DESC",
                    (user_id,),
                )
            elif lead_id:
                rows = db.execute(
                    "SELECT * FROM voice_calls WHERE lead_id=? ORDER BY started_at DESC",
                    (lead_id,),
                )
            else:
                rows = db.execute("SELECT * FROM voice_calls ORDER BY started_at DESC")
                
            calls = []
            for row in rows:
                calls.append({
                    "conversation_id": row[0],
                    "lead_id": row[1],
                    "user_id": row[2],
                    "status": row[4],
                    "started_at": row[3],
                    "ended_at": row[5],
                })
            return JsonResponse(calls, safe=False)
        except Exception:
            return JsonResponse(conversations, safe=False)
    except Exception as e:
        return JsonResponse({'detail': str(e)}, status=500)

@csrf_exempt
async def api_transfer_call_lead(request):
    if request.method != 'POST':
        return JsonResponse({'detail': 'Method not allowed'}, status=405)
        
    conversation_id = request.GET.get('conversation_id') or request.POST.get('conversation_id')
    lead_id = request.GET.get('lead_id') or request.POST.get('lead_id')
    
    if not conversation_id or not lead_id:
        # Try JSON body
        try:
            body = json.loads(request.body)
            conversation_id = conversation_id or body.get('conversation_id')
            lead_id = lead_id or body.get('lead_id')
        except Exception:
            pass
            
    if not conversation_id or not lead_id:
        return JsonResponse({'detail': 'conversation_id and lead_id are required'}, status=400)
        
    service = get_elevenlabs_service()
    try:
        conv = service.get_conversation(conversation_id)
        if not conv:
            return JsonResponse({'detail': 'Conversation not found'}, status=404)
        conv["lead_id"] = lead_id
        
        try:
            db = get_database()
            db.execute(
                "UPDATE voice_calls SET lead_id=? WHERE conversation_id=?",
                (lead_id, conversation_id),
            )
            db.commit()
        except Exception:
            pass
            
        return JsonResponse({
            "success": True,
            "conversation_id": conversation_id,
            "lead_id": lead_id,
        })
    except Exception as e:
        return JsonResponse({'detail': str(e)}, status=500)

@csrf_exempt
async def api_tutor_chat(request):
    if request.method != 'POST':
        return JsonResponse({'detail': 'Method not allowed'}, status=405)
        
    try:
        body = json.loads(request.body)
    except Exception:
        return JsonResponse({'detail': 'Invalid JSON body'}, status=400)
        
    message = body.get('message', '')
    user_id = body.get('user_id', '')
    question_context = body.get('question_context', '')
    session_id = body.get('session_id')
    assignment_id = body.get('assignment_id')
    question_id = body.get('question_id')
    
    if not message or not user_id:
        return JsonResponse({'detail': 'message and user_id are required'}, status=400)
        
    from asgiref.sync import sync_to_async
    from django.contrib.auth.models import User
    from .models import ChatSession, ChatMessage, Assignment, AssignmentQuestion

    @sync_to_async
    def process_chat_session():
        user = User.objects.filter(email=user_id).first()
        if not user:
            user = request.user if request.user.is_authenticated else None
            
        session = None
        if session_id:
            session = ChatSession.objects.filter(session_id=session_id, user=user).first()
            
        if not session and user:
            assignment = Assignment.objects.filter(code=assignment_id).first() if assignment_id else None
            question = AssignmentQuestion.objects.filter(code=question_id).first() if question_id else None
            title = f"Chat about {question.code}" if question else "New Chat"
            
            # If session_id was provided but not found, use it, else let default kick in
            if session_id:
                session = ChatSession.objects.create(session_id=session_id, user=user, assignment=assignment, question=question, title=title)
            else:
                session = ChatSession.objects.create(user=user, assignment=assignment, question=question, title=title)
                
        history = []
        if session:
            # fetch history
            history = [{"role": msg.role, "content": msg.content} for msg in session.messages.all().order_by('created_at')]
            # save user message
            ChatMessage.objects.create(session=session, role="user", content=message)
            
        return session, history

    session, history = await process_chat_session()
        
    service = get_tutor_agent_service()
    try:
        reply = await service.chat(message, user_id, question_context=question_context, history=history)
        
        if session:
            @sync_to_async
            def save_ai_message():
                ChatMessage.objects.create(session=session, role="assistant", content=reply)
                return session.title == "New Chat"
            needs_title = await save_ai_message()
            
            if needs_title:
                try:
                    from openai import AsyncOpenAI
                    import os
                    client = AsyncOpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
                    title_response = await client.chat.completions.create(
                        model="gpt-4o-mini",
                        messages=[
                            {"role": "system", "content": "You are a helpful assistant that generates extremely short (2-4 words) titles for chat conversations based on the user's first message. Just return the title, no quotes, no punctuation."},
                            {"role": "user", "content": message}
                        ],
                        max_tokens=10
                    )
                    new_title = title_response.choices[0].message.content.strip().strip('"').strip("'")
                    @sync_to_async
                    def save_title():
                        session.title = new_title
                        session.save()
                    await save_title()
                except Exception as e:
                    import logging
                    logging.getLogger(__name__).error(f"Failed to generate title: {e}")
        
        # Fire Inngest event so the user can review the LLM process in the background
        from app.services.inngest_client import inngest_client
        import inngest
        await inngest_client.send(
            inngest.Event(
                name="app/tutor.chat",
                data={
                    "message": message,
                    "user_id": user_id,
                    "answer": reply
                }
            )
        )

        return JsonResponse({"response": reply, "session_id": str(session.session_id) if session else None})
    except ValueError as exc:
        return JsonResponse({'detail': str(exc)}, status=400)
    except Exception as exc:
        logger.exception("Tutor chat failed for user_id=%s", user_id)
        return JsonResponse({'detail': 'Failed to reach tutor agent'}, status=500)

import asyncio
import uuid as _uuid

# Registry of active streaming requests: request_id -> asyncio.Event
_active_streams = {}

@csrf_exempt
async def api_tutor_chat_stream(request):
    """SSE endpoint that streams OpenAI tokens to the browser."""
    if request.method != 'POST':
        return JsonResponse({'detail': 'Method not allowed'}, status=405)

    try:
        body = json.loads(request.body)
    except Exception:
        return JsonResponse({'detail': 'Invalid JSON body'}, status=400)

    message = body.get('message', '')
    user_id = body.get('user_id', '')
    question_context = body.get('question_context', '')
    session_id = body.get('session_id')
    assignment_id = body.get('assignment_id')
    question_id = body.get('question_id')
    request_id = body.get('request_id', str(_uuid.uuid4()))

    if not message or not user_id:
        return JsonResponse({'detail': 'message and user_id are required'}, status=400)

    from asgiref.sync import sync_to_async
    from django.contrib.auth.models import User
    from .models import ChatSession, ChatMessage, Assignment, AssignmentQuestion

    @sync_to_async
    def process_chat_session():
        user = User.objects.filter(email=user_id).first()
        if not user:
            user = request.user if request.user.is_authenticated else None

        session = None
        if session_id:
            session = ChatSession.objects.filter(session_id=session_id, user=user).first()

        if not session and user:
            assignment = Assignment.objects.filter(code=assignment_id).first() if assignment_id else None
            question = AssignmentQuestion.objects.filter(code=question_id).first() if question_id else None
            title = f"Chat about {question.code}" if question else "New Chat"

            if session_id:
                session = ChatSession.objects.create(session_id=session_id, user=user, assignment=assignment, question=question, title=title)
            else:
                session = ChatSession.objects.create(user=user, assignment=assignment, question=question, title=title)

        history = []
        if session:
            history = [{"role": msg.role, "content": msg.content} for msg in session.messages.all().order_by('created_at')]
            ChatMessage.objects.create(session=session, role="user", content=message)

        return session, history

    session, history = await process_chat_session()

    service = get_tutor_agent_service()

    # Create a cancellation event for this request
    cancel_event = asyncio.Event()
    _active_streams[request_id] = cancel_event
    telemetry = {}

    async def event_stream():
        full_reply = ""
        was_cancelled = False
        try:
            async for token in service.chat_stream(
                message, user_id, question_context=question_context, history=history,
                cancel_event=cancel_event, telemetry=telemetry,
            ):
                if cancel_event.is_set():
                    was_cancelled = True
                    break
                full_reply += token
                yield f"data: {json.dumps({'token': token})}\n\n"
        except asyncio.CancelledError:
            # Client disconnected forcefully (e.g. AbortController)
            was_cancelled = True
        except Exception as exc:
            logger.exception("Streaming failed for user_id=%s", user_id)
            yield f"data: {json.dumps({'error': str(exc)})}\n\n"
            return
        finally:
            _active_streams.pop(request_id, None)
            # Ensure partial save happens in finally block so it can't be skipped by client disconnect
            if was_cancelled or (full_reply.strip() and not cancel_event.is_set() and not locals().get('needs_title')):
                try:
                    if session and full_reply.strip():
                        @sync_to_async
                        def save_partial():
                            # Check if we already saved it to prevent duplicates
                            if not ChatMessage.objects.filter(session=session, content=full_reply).exists():
                                ChatMessage.objects.create(session=session, role="assistant", content=full_reply)
                        await save_partial()
                except Exception as e:
                    logger.error(f"Failed to save partial reply: {e}")

        if was_cancelled:
            # We already saved in finally block, just yield cancelled if socket is still open
            try:
                yield f"data: {json.dumps({'cancelled': True, 'session_id': str(session.session_id) if session else None})}\n\n"
            except Exception:
                pass
            return

        # Signal the client that all tokens have been sent so it can
        # switch the Stop button back to Send immediately, before
        # the slower post-processing work runs.
        yield f"data: {json.dumps({'tokens_done': True})}\n\n"

        # --- Post-stream work (save message, title, inngest, memory) ---
        try:
            if session:
                @sync_to_async
                def save_ai_message():
                    # Message is already saved by the finally block in event_stream to prevent disconnect data loss
                    return session.title == "New Chat"
                needs_title = await save_ai_message()

                if needs_title:
                    try:
                        from openai import AsyncOpenAI
                        import os
                        client = AsyncOpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
                        title_response = await client.chat.completions.create(
                            model="gpt-4o-mini",
                            messages=[
                                {"role": "system", "content": "You are a helpful assistant that generates extremely short (2-4 words) titles for chat conversations based on the user's first message. Just return the title, no quotes, no punctuation."},
                                {"role": "user", "content": message}
                            ],
                            max_tokens=10
                        )
                        new_title = title_response.choices[0].message.content.strip().strip('"').strip("'")
                        @sync_to_async
                        def save_title():
                            session.title = new_title
                            session.save()
                        await save_title()
                    except Exception as e:
                        logger.error(f"Failed to generate title: {e}")

            # Fire Inngest event
            from app.services.inngest_client import inngest_client
            import inngest
            await inngest_client.send(
                inngest.Event(
                    name="app/tutor.chat",
                    data={
                        "message": message,
                        "user_id": user_id,
                        "answer": full_reply,
                        "metrics": telemetry
                    }
                )
            )

            # Save to mem0 memory
            await service.save_reply_and_memory(message, full_reply, user_id)
        except Exception as e:
            logger.error(f"Post-stream processing error: {e}")

        # Final SSE event to signal completion
        yield f"data: {json.dumps({'done': True, 'session_id': str(session.session_id) if session else None})}\n\n"

    response = StreamingHttpResponse(
        event_stream(),
        content_type='text/event-stream'
    )
    response['Cache-Control'] = 'no-cache'
    response['X-Accel-Buffering'] = 'no'
    return response

@csrf_exempt
async def api_tutor_chat_cancel(request):
    """Cancel an active streaming chat request."""
    if request.method != 'POST':
        return JsonResponse({'detail': 'Method not allowed'}, status=405)

    try:
        body = json.loads(request.body)
    except Exception:
        return JsonResponse({'detail': 'Invalid JSON body'}, status=400)

    request_id = body.get('request_id', '')
    if not request_id:
        return JsonResponse({'detail': 'request_id is required'}, status=400)

    cancel_event = _active_streams.get(request_id)
    if cancel_event:
        cancel_event.set()
        return JsonResponse({'cancelled': True})
    else:
        return JsonResponse({'cancelled': False, 'detail': 'No active stream found for this request_id'})

@csrf_exempt
async def api_create_openai_voice_session(request):
    if request.method != 'POST':
        return JsonResponse({'detail': 'Method not allowed'}, status=405)
        
    try:
        body = json.loads(request.body)
    except Exception:
        body = {}
        
    user_id = body.get('user_id', '')
    topic = body.get('topic', None)
    
    if not user_id:
        return JsonResponse({'detail': 'user_id is required'}, status=400)
        
    try:
        service = get_openai_voice_service()
        result = await service.create_realtime_session(
            user_id=user_id,
            topic=topic,
        )
        return JsonResponse(result)
    except ValueError as exc:
        return JsonResponse({'detail': str(exc)}, status=400)
    except Exception as exc:
        return JsonResponse({'detail': str(exc)}, status=500)

@csrf_exempt
async def api_store_openai_voice_memory(request):
    if request.method != 'POST':
        return JsonResponse({'detail': 'Method not allowed'}, status=405)
        
    try:
        body = json.loads(request.body)
    except Exception:
        return JsonResponse({'detail': 'Invalid JSON body'}, status=400)
        
    user_id = body.get('user_id')
    session_id = body.get('session_id')
    transcript = body.get('transcript', [])
    metrics = body.get('metrics', {})
    latest_user_message = body.get('latest_user_message')
    latest_assistant_message = body.get('latest_assistant_message')
    is_final = body.get('is_final', False)
    
    if not user_id or not session_id:
        return JsonResponse({'detail': 'user_id and session_id are required'}, status=400)
        
    try:
        service = get_openai_voice_service()
        await service.store_learning_memory(
            user_id=user_id,
            session_id=session_id,
            transcript=transcript,
            metrics=metrics,
            latest_user_message=latest_user_message,
            latest_assistant_message=latest_assistant_message,
            is_final=is_final,
        )
        return JsonResponse({"success": True})
    except ValueError as exc:
        return JsonResponse({'detail': str(exc)}, status=400)
    except Exception as exc:
        return JsonResponse({'detail': 'Failed to store voice memory'}, status=500)

@csrf_exempt
async def api_store_openai_voice_recording(request):
    if request.method != 'POST':
        return JsonResponse({'detail': 'Method not allowed'}, status=405)
        
    user_id = request.GET.get('user_id')
    session_id = request.GET.get('session_id')
    
    if not user_id or not session_id:
        return JsonResponse({'detail': 'user_id and session_id are required'}, status=400)
        
    # We yield the request body chunks asynchronously using an async generator helper
    async def get_body_chunks():
        # Django request.body contains the full uploaded payload
        yield request.body
        
    try:
        service = get_openai_voice_service()
        artifact = await service.store_recording(
            user_id=user_id,
            session_id=session_id,
            chunks=get_body_chunks(),
            content_type=request.headers.get("Content-Type"),
        )
        return JsonResponse({"success": True, "recording": artifact})
    except ValueError as exc:
        return JsonResponse({'detail': str(exc)}, status=400)
    except Exception as exc:
        return JsonResponse({'detail': 'Failed to store voice recording'}, status=500)


@login_required_custom()
def api_save_assignment_progress(request, assignment_id):
    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed'}, status=405)
        
    try:
        import json
        data = json.loads(request.body)
        answers = data.get('answers', {})
        
        user = get_session_user(request)
        if not user:
            return JsonResponse({'error': 'Unauthorized'}, status=401)
            
        student_profile = models.UserProfile.objects.filter(user__username=user['id']).first()
        if not student_profile:
            student_profile = models.UserProfile.objects.filter(role__role_code='student').first()
            
        assignment_obj = models.Assignment.objects.filter(code=assignment_id).first()
        
        if student_profile and assignment_obj:
            prog_obj = models.StudentTopicAssessmentReview.objects.filter(
                student=student_profile,
                assignment=assignment_obj
            ).first()
            
            if prog_obj:
                prog_obj.saved_answers = answers
                prog_obj.save()
                return JsonResponse({'success': True})
                
        return JsonResponse({'error': 'Assessment record not found'}, status=404)
        
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.error("Failed to save assignment progress: %s", str(e))
        return JsonResponse({'error': str(e)}, status=500)

@login_required_custom()
def api_complete_assignment(request, assignment_id):
    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed'}, status=405)
        
    try:
        import json
        data = json.loads(request.body)
        score = data.get('score', 0)
        answers = data.get('answers', {})
        
        user = get_session_user(request)
        if not user:
            return JsonResponse({'error': 'Unauthorized'}, status=401)
            
        student_profile = models.UserProfile.objects.filter(user__username=user['id']).first()
        if not student_profile:
            student_profile = models.UserProfile.objects.filter(role__role_code='student').first()
            
        assignment_obj = models.Assignment.objects.filter(code=assignment_id).first()
        
        if student_profile and assignment_obj:
            prog_obj = models.StudentTopicAssessmentReview.objects.filter(
                student=student_profile,
                assignment=assignment_obj
            ).first()
            
            if prog_obj:
                prog_obj.status = 'completed'
                prog_obj.score = score
                prog_obj.questions_completed = prog_obj.total_questions
                prog_obj.saved_answers = answers
                prog_obj.save()
                return JsonResponse({'success': True, 'status': 'completed', 'score': score})
                
        return JsonResponse({'error': 'Assessment record not found'}, status=404)
        
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.error("Failed to update assignment status: %s", str(e))
        return JsonResponse({'error': 'Server error'}, status=500)


@login_required_custom()
def api_update_time_spent(request, assignment_id):
    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed'}, status=405)
        
    try:
        import json
        data = json.loads(request.body)
        delta_seconds = data.get('delta_seconds', 0)
        
        user = get_session_user(request)
        if not user:
            return JsonResponse({'error': 'Unauthorized'}, status=401)
            
        student_profile = models.UserProfile.objects.filter(user__username=user['id']).first()
        if not student_profile:
            student_profile = models.UserProfile.objects.filter(role__role_code='student').first()
            
        assignment_obj = models.Assignment.objects.filter(code=assignment_id).first()
        
        if student_profile and assignment_obj:
            prog_obj = models.StudentTopicAssessmentReview.objects.filter(
                student=student_profile,
                assignment=assignment_obj
            ).first()
            
            if prog_obj:
                prog_obj.time_spent += delta_seconds
                prog_obj.save()
                return JsonResponse({'success': True, 'time_spent': prog_obj.time_spent})
                
        return JsonResponse({'error': 'Assessment record not found'}, status=404)
        
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.error("Failed to update time spent: %s", str(e))
        return JsonResponse({'error': 'Server error'}, status=500)




def guided_assignment_view(request, session_id):
    if f'guided_session_{session_id}' not in request.session:
        # If session not found, redirect to dashboard
        return redirect('voice_tutor:professor_dashboard')
        
    session_data = request.session[f'guided_session_{session_id}']
    context = {
        'session_id': session_id,
        'subject': session_data.get('subject'),
        'topic': session_data.get('topic'),
        'files': session_data.get('files', [])
    }
    return render(request, 'professor/guided_assignment_chat.html', context)

@csrf_exempt
async def api_guided_assignment_chat(request, session_id):
    import json
    from services.guided_assignment_agent import get_guided_assignment_agent_service
    
    if request.method != "POST":
        return JsonResponse({"detail": "Method not allowed"}, status=405)

    try:
        body = json.loads(request.body)
        message = body.get("message", "").strip()

        if not message:
            return JsonResponse({"detail": "Message is empty"}, status=400)

        # Get session context
        from asgiref.sync import sync_to_async
        @sync_to_async(thread_sensitive=False)
        def get_session_data():
            return request.session.get(f'guided_session_{session_id}')
            
        session_data = await get_session_data()
        
        if not session_data:
            return JsonResponse({"detail": "Session expired or invalid"}, status=400)

        subject = session_data.get('subject')
        topic = session_data.get('topic')
        batch_id = session_data.get('batch_id')

        # Run the agent
        agent_service = get_guided_assignment_agent_service()
        response_text, assignment_created = await agent_service.chat(
            message=message,
            session_id=session_id,
            subject=subject,
            topic=topic,
            batch_id=batch_id
        )

        return JsonResponse({"response": response_text, "assignment_created": assignment_created})
    except Exception as e:
        logger.error("Guided assignment chat error: %s", str(e), exc_info=True)
        return JsonResponse({"detail": str(e)}, status=500)

@login_required_custom(role='student')
def student_notes_directory(request):
    user = get_session_user(request)
    student_profile = models.UserProfile.objects.filter(user__username=user['id']).first()
    if not student_profile:
        student_profile = models.UserProfile.objects.filter(role__role_code='student').first()
        
    student_batchs_qs = models.Batch.objects.filter(students=student_profile)
    # Get all subjects the student is enrolled in
    student_subjects = models.SubjectMaster.objects.filter(
        allocations__batch__in=student_batchs_qs
    ).distinct()

    # Get notes for those subjects
    notes = models.SummaryNotes.objects.filter(
        topic__subject__in=student_subjects
    ).select_related('topic', 'topic__subject').order_by('-created_at')

    context = {
        'notes': notes,
        'active_tab': 'notes'
    }
    return render(request, 'student/notes_directory.html', context)

@login_required_custom(role='student')
def student_note_detail(request, note_id):
    note = get_object_or_404(models.SummaryNotes, id=note_id)
    from app.services.notes_pipeline import strip_display_metadata
    
    source_assignment = models.Assignment.objects.filter(
        topic=note.topic, 
        source_pdf_path__isnull=False
    ).exclude(source_pdf_path='').first()
    
    context = {
        'note': note,
        'note_content': strip_display_metadata(note.content),
        'active_tab': 'notes',
        'source_assignment': source_assignment
    }
    return render(request, 'student/view_notes.html', context)


@csrf_exempt
async def api_get_chat_sessions(request):
    if request.method != 'GET':
        return JsonResponse({'detail': 'Method not allowed'}, status=405)
        
    from asgiref.sync import sync_to_async
    from .models import ChatSession
    from django.contrib.auth.models import User
    
    @sync_to_async
    def fetch_sessions():
        session_user = request.session.get('user')
        if not session_user:
            return None
        email = session_user.get('id')
        user = User.objects.filter(username=email).first() or User.objects.filter(email=email).first()
        if not user:
            return None
            
        sessions = ChatSession.objects.filter(user=user).select_related('assignment', 'question')
        return [
            {
                "session_id": str(s.session_id),
                "title": s.title,
                "assignment_id": s.assignment.code if s.assignment else None,
                "question_id": s.question.code if s.question else None,
                "updated_at": s.updated_at.isoformat()
            } for s in sessions
        ]
        
    try:
        data = await fetch_sessions()
        if data is None:
            return JsonResponse({'detail': 'Not authenticated'}, status=401)
        return JsonResponse({"sessions": data})
    except Exception as e:
        return JsonResponse({'detail': str(e)}, status=500)


@csrf_exempt
async def api_get_chat_history(request, session_id):
    if request.method != 'GET':
        return JsonResponse({'detail': 'Method not allowed'}, status=405)
        
    from asgiref.sync import sync_to_async
    from .models import ChatSession
    from django.contrib.auth.models import User
    
    @sync_to_async
    def fetch_history():
        session_user = request.session.get('user')
        if not session_user:
            return "UNAUTH"
        email = session_user.get('id')
        user = User.objects.filter(username=email).first() or User.objects.filter(email=email).first()
        if not user:
            return "UNAUTH"
            
        session = ChatSession.objects.filter(session_id=session_id, user=user).first()
        if not session:
            return None
        return [
            {
                "role": msg.role,
                "content": msg.content,
                "created_at": msg.created_at.isoformat()
            } for msg in session.messages.all()
        ]
        
    try:
        history = await fetch_history()
        if history == "UNAUTH":
            return JsonResponse({'detail': 'Not authenticated'}, status=401)
        if history is None:
            return JsonResponse({'detail': 'Session not found'}, status=404)
        return JsonResponse({"messages": history})
    except Exception as e:
        return JsonResponse({'detail': str(e)}, status=500)


@csrf_exempt
async def api_delete_chat_session(request, session_id):
    if request.method != 'DELETE':
        return JsonResponse({'detail': 'Method not allowed'}, status=405)
        
    from asgiref.sync import sync_to_async
    from .models import ChatSession
    from django.contrib.auth.models import User
    
    @sync_to_async
    def delete_session():
        session_user = request.session.get('user')
        if not session_user:
            return "UNAUTH"
        email = session_user.get('id')
        user = User.objects.filter(username=email).first() or User.objects.filter(email=email).first()
        if not user:
            return "UNAUTH"
            
        session = ChatSession.objects.filter(session_id=session_id, user=user).first()
        if not session:
            return None
        session.delete()
        return True
        
    try:
        result = await delete_session()
        if result == "UNAUTH":
            return JsonResponse({'detail': 'Not authenticated'}, status=401)
        if result is None:
            return JsonResponse({'detail': 'Session not found'}, status=404)
        return JsonResponse({"detail": "Session deleted"})
    except Exception as e:
        return JsonResponse({'detail': str(e)}, status=500)


def _get_assignment_oral_defense_config(assignment_id=None):
    """Load a published game config, preserving the original calculus trial as legacy content."""
    from services.oral_defense_service import legacy_oral_defense_config

    if not assignment_id:
        return legacy_oral_defense_config()
    assignment_obj = models.Assignment.objects.filter(code=assignment_id).first()
    if not assignment_obj:
        return None
    if assignment_obj.oral_defense_enabled and isinstance(assignment_obj.oral_defense_config, dict):
        return assignment_obj.oral_defense_config
    is_legacy_trial = (
        assignment_obj.code == 'A_C2F6D9'
        or 'maxima and minima' in assignment_obj.title.lower()
        or 'profit maximization' in assignment_obj.title.lower()
    )
    return legacy_oral_defense_config() if is_legacy_trial else None


@login_required_custom()
def oral_defense_view(request, assignment_id):
    """
    Configurable AI Voice Oral Defense (Mini Boss Battle).
    """
    assignment_obj = models.Assignment.objects.filter(code=assignment_id).first()
    if not assignment_obj:
        return redirect('voice_tutor:student_dashboard')

    game_config = _get_assignment_oral_defense_config(assignment_obj.code)
    if not game_config:
        return redirect('voice_tutor:take_assignment', assignment_id=assignment_obj.code)

    user = get_session_user(request)
    first_name = "Scholar"
    if user and user.get('name'):
        first_name = user['name'].split()[0]

    levels = game_config.get('levels', [])
    first_level = levels[0]
    initial_hp = max(100, len(levels) * 50)
    topic_name = assignment_obj.topic.name if assignment_obj.topic else assignment_obj.title
    context = {
        'active_tab': 'assignments',
        'assignment': assignment_obj,
        'first_name': first_name,
        'boss_name': game_config.get('boss_name', 'Professor Algor'),
        'boss_title': game_config.get('boss_title', 'Oral Defense Examiner'),
        'game_title': game_config.get('title', f'{topic_name} Oral Defense'),
        'topic_name': topic_name,
        'game_levels': levels,
        'first_level': first_level,
        'total_levels': len(levels),
        'initial_hp': initial_hp,
    }
    return render(request, 'student/oral_defense.html', context)


@csrf_exempt
async def api_oral_defense_intro(request):
    """Returns the introductory challenge question and OpenAI voice audio for the boss battle round."""
    if request.method not in ['GET', 'POST']:
        return JsonResponse({'detail': 'Method not allowed'}, status=405)
    
    round_num = 1
    if request.method == 'GET':
        try:
            round_num = int(request.GET.get('round', 1))
        except (ValueError, TypeError):
            round_num = 1
        assignment_id = request.GET.get('assignment_id')
    else:
        try:
            body = json.loads(request.body)
            round_num = int(body.get('round', 1))
            assignment_id = body.get('assignment_id')
        except Exception:
            round_num = 1
            assignment_id = None
            
    from services.oral_defense_service import get_oral_defense_service
    service = get_oral_defense_service()
    try:
        from asgiref.sync import sync_to_async
        game_config = await sync_to_async(_get_assignment_oral_defense_config)(assignment_id)
        if not game_config:
            return JsonResponse({'detail': 'Oral Defense is not enabled for this assignment.'}, status=404)
        data = await service.get_intro_audio(round_num, game_config=game_config)
        return JsonResponse(data)
    except Exception as e:
        return JsonResponse({'detail': str(e)}, status=500)


@csrf_exempt
async def api_oral_defense_turn(request):
    """Evaluates student spoken defense and returns OpenAI voice audio with damage calculation."""
    if request.method != 'POST':
        return JsonResponse({'detail': 'Method not allowed'}, status=405)

    try:
        body = json.loads(request.body)
    except Exception:
        return JsonResponse({'detail': 'Invalid JSON body'}, status=400)

    try:
        round_num = int(body.get('round', 1))
    except (ValueError, TypeError):
        round_num = 1

    transcript = body.get('transcript', '')
    assignment_id = body.get('assignment_id')
    try:
        current_boss_hp = int(body.get('boss_hp', 100))
    except (ValueError, TypeError):
        current_boss_hp = 100

    from services.oral_defense_service import get_oral_defense_service
    service = get_oral_defense_service()
    try:
        from asgiref.sync import sync_to_async
        game_config = await sync_to_async(_get_assignment_oral_defense_config)(assignment_id)
        if not game_config:
            return JsonResponse({'detail': 'Oral Defense is not enabled for this assignment.'}, status=404)
        result = await service.evaluate_turn(
            round_num=round_num,
            student_transcript=transcript,
            current_boss_hp=current_boss_hp,
            game_config=game_config,
        )
        return JsonResponse(result)
    except Exception as e:
        return JsonResponse({'detail': str(e)}, status=500)


@csrf_exempt
async def api_oral_defense_sage(request):
    """
    Sage AI Corner Coach endpoint:
    - action='perspective': returns intuitive breakdown for angle ('calculus', 'graph', 'business')
    - action='ask': returns real-time Socratic coaching for student's custom question
    """
    if request.method != 'POST':
        return JsonResponse({'detail': 'Method not allowed'}, status=405)

    try:
        body = json.loads(request.body)
    except Exception:
        return JsonResponse({'detail': 'Invalid JSON body'}, status=400)

    try:
        round_num = int(body.get('round', 1))
    except (ValueError, TypeError):
        round_num = 1

    action = body.get('action', 'perspective')
    assignment_id = body.get('assignment_id')
    from services.oral_defense_service import get_oral_defense_service
    service = get_oral_defense_service()

    try:
        from asgiref.sync import sync_to_async
        game_config = await sync_to_async(_get_assignment_oral_defense_config)(assignment_id)
        if not game_config:
            return JsonResponse({'detail': 'Oral Defense is not enabled for this assignment.'}, status=404)
        if action == 'ask':
            question = body.get('question', '')
            result = await service.ask_sage_coach(round_num, question, game_config=game_config)
        else:
            angle = body.get('angle', 'calculus')
            result = await service.get_sage_perspective(round_num, angle, game_config=game_config)
        return JsonResponse(result)
    except Exception as e:
        return JsonResponse({'detail': str(e)}, status=500)


async def api_visualize_concept(request):
    """
    Endpoint that accepts a question or concept, triggers the visualization pipeline (LLM tool calling + physics validation),
    and returns a structured simulation specification.
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed'}, status=405)

    try:
        body = json.loads(request.body)
    except Exception:
        return JsonResponse({'error': 'Invalid JSON body'}, status=400)

    question_text = body.get('question_text', '').strip()
    context = body.get('context', '').strip()

    if not question_text:
        return JsonResponse({'error': 'question_text is required'}, status=400)

    from services.visualization_service import (
        VisualizationValidationError,
        get_visualization_service,
        is_question_visualizable,
    )
    if not is_question_visualizable(question_text):
        return JsonResponse({
            'status': 'not_applicable',
            'error': 'This question tests conceptual or definitional recall rather than a dynamic STEM simulation.'
        }, status=400)

    vis_service = get_visualization_service()

    try:
        sim_data = await vis_service.plan_simulation(question_text=question_text, context=context)
        return JsonResponse({'status': 'success', 'simulation': sim_data})
    except VisualizationValidationError as exc:
        return JsonResponse({'status': 'not_applicable', 'error': str(exc)}, status=422)
    except Exception as e:
        logger.exception("Visualization generation failed: %s", e)
        return JsonResponse({
            'status': 'error',
            'error': 'The visualization could not be generated and verified.'
        }, status=502)


# -----------------------------------------------------------------------------
# Plan Mode Views & APIs
# -----------------------------------------------------------------------------

@login_required_custom(role='professor')
def professor_plan(request):
    user = get_session_user(request)
    prof_profile = models.UserProfile.objects.filter(user__username=user['id']).first()
    if not prof_profile:
        prof_profile = models.UserProfile.objects.filter(role__role_code='professor').first()

    batches = []
    subjects = []
    if prof_profile:
        allocations = models.ProfessorAllocation.objects.filter(professor=prof_profile).select_related('batch', 'subject')
        batches = list({a.batch for a in allocations if a.batch})
        subjects = list({a.subject for a in allocations if a.subject})

    if not batches:
        batches = list(models.Batch.objects.all())
    if not subjects:
        subjects = list(models.SubjectMaster.objects.all())

    initial_topic = request.GET.get('topic', '').strip()
    initial_course_name = request.GET.get('course_name', '').strip()
    initial_subject = request.GET.get('subject', '').strip()
    initial_batch = request.GET.get('batch', '').strip()

    context = {
        'active_tab': 'plan',
        'batches': batches,
        'subjects': subjects,
        'initial_topic': initial_topic,
        'initial_course_name': initial_course_name,
        'initial_subject': initial_subject,
        'initial_batch': initial_batch,
    }
    return render(request, 'professor/plan.html', context)


@csrf_exempt
@login_required_custom(role='professor')
def api_plan_transcribe(request):
    """Transcribe a short professor recording for the no-PDF topic brief."""
    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed'}, status=405)

    audio = request.FILES.get('audio')
    if not audio:
        return JsonResponse({'error': 'Record something before asking Sage to transcribe it.'}, status=400)
    if audio.size > 10 * 1024 * 1024:
        return JsonResponse({'error': 'Keep voice recordings under 10 MB.'}, status=400)

    content_type = (audio.content_type or 'audio/webm').lower()
    base_content_type = content_type.split(';', 1)[0].strip()
    base_content_type = {
        'video/webm': 'audio/webm',
        'video/mp4': 'audio/mp4',
    }.get(base_content_type, base_content_type)
    allowed_types = {
        'audio/webm', 'audio/mp4', 'audio/ogg', 'audio/wav', 'audio/x-wav',
        'audio/mpeg', 'audio/mp3', 'audio/aac', 'audio/flac',
    }
    if base_content_type not in allowed_types:
        return JsonResponse({'error': 'This browser produced an unsupported audio format.'}, status=400)

    try:
        audio.seek(0)
        audio_bytes = audio.read()
        if len(audio_bytes) < 1000:
            return JsonResponse({'error': 'That recording was too short. Speak for at least two seconds.'}, status=400)
        service = get_openai_voice_service()
        transcript = service.transcribe_audio(
            audio_bytes,
            filename=audio.name or 'topic-brief.webm',
            content_type=base_content_type,
        )
    except ValueError as exc:
        return JsonResponse({'error': str(exc)}, status=400)
    except Exception:
        logger.exception('Plan Mode voice transcription failed')
        return JsonResponse({'error': 'Sage could not transcribe that recording right now.'}, status=502)

    return JsonResponse({'success': True, 'text': transcript})


@csrf_exempt
def api_plan_upload(request):
    """
    Handles PDF upload, topic briefs, or sample selection for Plan Mode.
    Generates initial notes and prepares a question bank from the selected source.
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed'}, status=405)

    from services.plan_mode_service import (
        build_course_context,
        extract_pdf_data,
        generate_course_from_brief,
        generate_notes_with_ai,
        generate_questions_with_ai,
        DEFAULT_SAMPLE_NOTES,
        DEFAULT_SAMPLE_QUESTIONS,
        DEFAULT_FOCUS_AREAS,
    )
    from config import get_settings
    from app.services.inngest_functions import update_job_status
    from app.services.ingestion_artifacts import sha256_file, write_pages_artifact
    settings = get_settings()
    plan_user = get_session_user(request) or {}
    tenant_id = str(plan_user.get('id') or 'default')

    job_id = request.POST.get('job_id') or uuid.uuid4().hex
    is_sample = request.POST.get('sample') == 'true'
    uploaded_file = request.FILES.get('file')
    subject = request.POST.get('subject', 'Machine Learning').strip()
    batch = request.POST.get('batch', 'Year 2').strip()
    course_name = request.POST.get('course_name', '').strip()
    topic = request.POST.get('topic', '').strip()
    description = request.POST.get('description', '').strip()
    is_brief = bool(description)

    if not course_name:
        return JsonResponse({'error': 'Add a course name before giving Sage your material.'}, status=400)
    if len(course_name) > 120:
        return JsonResponse({'error': 'Keep the course name under 120 characters.'}, status=400)

    update_job_status(job_id, "Understanding topic" if is_brief else "Reading pages", 15, step=1)

    if is_brief:
        if not topic:
            return JsonResponse({'error': 'Add a topic before asking Sage to prepare the course.'}, status=400)
        if len(topic) > 180:
            return JsonResponse({'error': 'Keep the topic under 180 characters.'}, status=400)
        if len(description) < 20:
            return JsonResponse({'error': 'Describe what you want students to learn in at least 20 characters.'}, status=400)
        if len(description) > 6000:
            return JsonResponse({'error': 'Keep the topic description under 6,000 characters.'}, status=400)

    file_path = ""
    filename = "Topic brief" if is_brief else ("K-Means Clustering.pdf" if is_sample else "Uploaded Material.pdf")
    pages_count = 12
    context_text = ""
    source_pages = []
    file_sha256 = ""
    pages_artifact_path = ""

    if is_brief:
        pages_count = 0
        context_text = description
        source_pages = [{"page_number": 1, "text": description}]
    elif is_sample or not uploaded_file:
        # Check for local sample PDF
        sample_path = settings.DATA_DIR / "uploads" / "ML" / "k-means" / "K_Means.pdf"
        if sample_path.exists():
            file_path = str(sample_path)
            extracted = extract_pdf_data(file_path)
            pages_count = extracted.get("page_count", 12) or 12
            context_text = extracted.get("full_text", "")
            source_pages = extracted.get("pages", [])
        if not topic:
            topic = "K-Means Clustering"
    else:
        filename = uploaded_file.name
        if not topic:
            topic = Path(filename).stem.replace('-', ' ').replace('_', ' ').title()

        save_dir = (
            settings.UPLOADS_DIR
            / "plan_mode"
            / (subject.replace(' ', '_') or "General")
            / job_id
        )
        save_dir.mkdir(parents=True, exist_ok=True)
        filename = Path(filename).name
        dest_path = save_dir / filename
        digest = hashlib.sha256()
        with open(dest_path, 'wb+') as dest:
            for chunk in uploaded_file.chunks():
                digest.update(chunk)
                dest.write(chunk)
        file_path = str(dest_path)
        file_sha256 = digest.hexdigest()

        extracted = extract_pdf_data(file_path)
        pages_count = extracted.get("page_count", 1) or 1
        context_text = extracted.get("full_text", "")
        source_pages = extracted.get("pages", [])

    # Plan Mode needs these pages synchronously. Persist that one extraction so
    # the background RAG job can reuse it instead of opening/OCRing the PDF again.
    if file_path and source_pages:
        file_sha256 = file_sha256 or sha256_file(file_path)
        pages_artifact_path = write_pages_artifact(
            job_id,
            source_pages,
            file_sha256=file_sha256,
        )

    # Step 1 (Reading pages) is complete! Move to Step 2 (Finding key concepts)
    update_job_status(job_id, "Building course structure" if is_brief else "Finding key concepts", 45, step=2)

    # Dispatch Inngest pipeline event so logs and execution appear in the Inngest dev server
    if file_path:
        try:
            from app.services.inngest_client import inngest_client
            import inngest
            from asgiref.sync import async_to_sync

            ingestion_job_id = f"{job_id}_rag"
            inngest_evt = inngest.Event(
                name="app/document.uploaded",
                data={
                    "subject": subject,
                    "topic": topic,
                    "file_path": str(file_path),
                    "filename": filename,
                    "job_id": ingestion_job_id,
                    "parent_job_id": job_id,
                    "pages_artifact_path": pages_artifact_path,
                    "file_sha256": file_sha256,
                    "tenant_id": tenant_id,
                }
            )
            async_to_sync(inngest_client.send)(inngest_evt)
            logger.info(
                "Dispatched Inngest event 'app/document.uploaded' for %s (job_id: %s)",
                filename,
                ingestion_job_id,
            )
        except Exception as inngest_err:
            logger.warning(f"Could not dispatch Inngest event: {inngest_err}")

    # Step 2 complete -> Moving to Step 3: Writing short notes & questions
    update_job_status(job_id, "Writing short notes", 75, step=3)

    # Generate initial notes and questions
    try:
        if is_brief:
            notes_data = generate_course_from_brief(subject, topic, description)
        else:
            notes_data = generate_notes_with_ai(
                subject,
                topic,
                context_text,
                pages=source_pages,
                source_file=filename,
            )
    except ValueError as exc:
        return JsonResponse({'error': str(exc)}, status=400)

    notes = notes_data.get("notes", DEFAULT_SAMPLE_NOTES)
    focus_areas = notes_data.get("focus_areas", DEFAULT_FOCUS_AREAS)
    if is_brief:
        context_text = build_course_context(topic, description, notes)
        source_pages = [{"page_number": 1, "text": context_text}]
    try:
        questions = generate_questions_with_ai(
            subject,
            topic,
            context_text,
            count=6,
            difficulty="Mixed",
            focus_areas=focus_areas,
            source_has_pages=not is_brief,
            professor_brief=description if is_brief else "",
        )
    except ValueError as exc:
        return JsonResponse({'error': str(exc)}, status=502)

    update_job_status(job_id, "Ready", 100, step=3)

    # Store in session for subsequent wizard steps
    session_id = f"plan_{uuid.uuid4().hex[:8]}"
    request.session['plan_session'] = {
        'session_id': session_id,
        'job_id': job_id,
        'course_name': course_name,
        'topic': topic,
        'subject': subject,
        'batch': batch,
        'filename': filename,
        'file_path': file_path,
        'source_type': 'brief' if is_brief else 'pdf',
        'brief_description': description if is_brief else '',
        'context_text': context_text,
        'source_pages': source_pages,
        'notes': notes,
        'note_chats': {},
        'questions': questions,
        'focus_areas': focus_areas,
    }

    return JsonResponse({
        'success': True,
        'session_id': session_id,
        'job_id': job_id,
        'course_name': course_name,
        'topic': topic,
        'subject': subject,
        'batch': batch,
        'filename': filename,
        'page_count': pages_count,
        'source_type': 'brief' if is_brief else 'pdf',
        'notes': notes,
        'questions': questions,
        'focus_areas': focus_areas,
    })


@csrf_exempt
def api_plan_status(request, job_id):
    """
    Returns live progress and step info for the uploaded PDF pipeline.
    """
    if request.method != "GET":
        return JsonResponse({"detail": "Method not allowed"}, status=405)

    from config import get_settings
    import json
    settings = get_settings()
    job_file = settings.UPLOADS_DIR / "jobs" / f"{job_id}.json"

    if not job_file.exists():
        return JsonResponse({"step": 1, "progress": 10, "status": "Reading pages"}, status=200)

    try:
        with open(job_file, "r") as jf:
            data = json.load(jf)
        return JsonResponse(data, status=200)
    except Exception as e:
        return JsonResponse({"step": 1, "progress": 10, "status": "Reading pages"}, status=200)


@csrf_exempt
def api_plan_notes_regenerate(request):
    """
    Regenerates study notes from the uploaded material or session.
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed'}, status=405)

    try:
        body = json.loads(request.body.decode('utf-8'))
    except Exception:
        body = request.POST

    from services.plan_mode_service import (
        build_course_context,
        generate_course_from_brief,
        generate_notes_with_ai,
        DEFAULT_SAMPLE_NOTES,
        DEFAULT_FOCUS_AREAS,
    )

    session_data = request.session.get('plan_session', {})
    subject = body.get('subject') or session_data.get('subject', 'Machine Learning')
    topic = body.get('topic') or session_data.get('topic', 'General')
    context_text = session_data.get('context_text', '')

    if session_data.get('source_type') == 'brief':
        description = session_data.get('brief_description', '')
        try:
            res = generate_course_from_brief(subject, topic, description)
        except ValueError as exc:
            return JsonResponse({'error': str(exc)}, status=400)
    else:
        res = generate_notes_with_ai(
            subject,
            topic,
            context_text,
            pages=session_data.get('source_pages'),
            source_file=session_data.get('filename', 'Uploaded material.pdf'),
        )
    notes = res.get('notes', DEFAULT_SAMPLE_NOTES)
    focus_areas = res.get('focus_areas', DEFAULT_FOCUS_AREAS)

    if session_data.get('source_type') == 'brief':
        context_text = build_course_context(topic, description, notes)
        session_data['context_text'] = context_text
        session_data['source_pages'] = [{'page_number': 1, 'text': context_text}]

    session_data['notes'] = notes
    session_data['note_chats'] = {}
    session_data['focus_areas'] = focus_areas
    request.session['plan_session'] = session_data

    return JsonResponse({
        'success': True,
        'notes': notes,
        'focus_areas': focus_areas,
    })


@csrf_exempt
@login_required_custom(role='professor')
def api_plan_note_refine(request):
    """Let a professor revise one note section through a short Sage conversation."""
    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed'}, status=405)

    try:
        body = json.loads(request.body.decode('utf-8'))
    except Exception:
        return JsonResponse({'error': 'Invalid JSON body'}, status=400)

    instruction = str(body.get('instruction', '')).strip()
    if not instruction:
        return JsonResponse({'error': 'Tell Sage what you want to change.'}, status=400)
    if len(instruction) > 2000:
        return JsonResponse({'error': 'Keep the instruction under 2,000 characters.'}, status=400)

    try:
        section_index = int(body.get('section_index'))
    except (TypeError, ValueError):
        return JsonResponse({'error': 'Invalid note section.'}, status=400)

    session_data = request.session.get('plan_session', {})
    notes = session_data.get('notes', [])
    if section_index < 0 or section_index >= len(notes):
        return JsonResponse({'error': 'That note section is no longer available.'}, status=404)

    supplied_section = body.get('section')
    current_section = supplied_section if isinstance(supplied_section, dict) else notes[section_index]
    chat_store = session_data.setdefault('note_chats', {})
    history = chat_store.get(str(section_index), [])

    try:
        from services.plan_mode_service import refine_note_section_with_ai

        result = refine_note_section_with_ai(
            subject=body.get('subject') or session_data.get('subject', 'General'),
            topic=body.get('topic') or session_data.get('topic', 'General'),
            section=current_section,
            instruction=instruction,
            pages=session_data.get('source_pages', []),
            conversation=history,
        )
    except ValueError as exc:
        return JsonResponse({'error': str(exc)}, status=400)
    except Exception as exc:
        logger.exception('Plan Mode section refinement failed')
        return JsonResponse({'error': 'Sage could not revise this section right now.'}, status=500)

    notes[section_index] = result['section']
    history.extend([
        {'role': 'user', 'content': instruction},
        {'role': 'assistant', 'content': result['message']},
    ])
    chat_store[str(section_index)] = history[-10:]
    session_data['notes'] = notes
    session_data['note_chats'] = chat_store
    request.session['plan_session'] = session_data
    request.session.modified = True

    return JsonResponse({
        'success': True,
        'section': result['section'],
        'message': result['message'],
    })


@csrf_exempt
def api_plan_questions_generate(request):
    """
    Generates questions matching specified count, difficulty, and focus areas.
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed'}, status=405)

    try:
        body = json.loads(request.body.decode('utf-8'))
    except Exception:
        body = request.POST

    from services.plan_mode_service import generate_questions_with_ai

    session_data = request.session.get('plan_session', {})
    subject = body.get('subject') or session_data.get('subject', 'Machine Learning')
    topic = body.get('topic') or session_data.get('topic', 'K-Means Clustering')
    context_text = session_data.get('context_text', '')
    count = int(body.get('count', 6))
    difficulty = body.get('difficulty', 'Mixed')
    focus_areas = body.get('focus', [])

    is_brief = session_data.get('source_type') == 'brief'
    try:
        questions = generate_questions_with_ai(
            subject=subject,
            topic=topic,
            context_text=context_text,
            count=count,
            difficulty=difficulty,
            focus_areas=focus_areas,
            source_has_pages=not is_brief,
            professor_brief=session_data.get('brief_description', '') if is_brief else '',
        )
    except ValueError as exc:
        return JsonResponse({'error': str(exc)}, status=502)

    session_data['questions'] = questions
    request.session['plan_session'] = session_data

    return JsonResponse({
        'success': True,
        'questions': questions,
    })


@csrf_exempt
def api_plan_questions_swap(request):
    """
    Swaps a single question with a new alternative targeting the specified focus area.
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed'}, status=405)

    try:
        body = json.loads(request.body.decode('utf-8'))
    except Exception:
        body = request.POST

    from services.plan_mode_service import swap_question_with_ai

    session_data = request.session.get('plan_session', {})
    subject = body.get('subject') or session_data.get('subject', 'Machine Learning')
    topic = body.get('topic') or session_data.get('topic', 'K-Means Clustering')
    context_text = session_data.get('context_text', '')
    focus = body.get('focus', 'Algorithm')
    difficulty = body.get('difficulty', 'Medium')
    existing_questions = session_data.get('questions', [])

    is_brief = session_data.get('source_type') == 'brief'
    try:
        new_q = swap_question_with_ai(
            subject=subject,
            topic=topic,
            context_text=context_text,
            focus=focus,
            difficulty=difficulty,
            existing_questions=existing_questions,
            source_has_pages=not is_brief,
            professor_brief=session_data.get('brief_description', '') if is_brief else '',
        )
    except ValueError as exc:
        return JsonResponse({'error': str(exc)}, status=502)

    return JsonResponse({
        'success': True,
        'question': new_q,
    })


@csrf_exempt
@login_required_custom(role='professor')
def api_plan_question_visualize(request):
    """Generate and retain a professor-reviewable visual for one draft question."""
    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed'}, status=405)

    try:
        body = json.loads(request.body.decode('utf-8'))
    except Exception:
        return JsonResponse({'error': 'Invalid JSON body'}, status=400)

    try:
        question_index = int(body.get('question_index'))
    except (TypeError, ValueError):
        return JsonResponse({'error': 'Invalid question.'}, status=400)

    supplied_question = body.get('question')
    question_text = str(
        supplied_question.get('q', '') if isinstance(supplied_question, dict)
        else body.get('question_text', '')
    ).strip()
    if not question_text:
        return JsonResponse({'error': 'Add question text before generating a visual.'}, status=400)
    if len(question_text) > 4000:
        return JsonResponse({'error': 'Keep the question under 4,000 characters.'}, status=400)

    session_data = request.session.get('plan_session', {})
    questions = session_data.get('questions', [])
    if question_index < 0 or question_index >= len(questions):
        return JsonResponse({'error': 'That draft question is no longer available.'}, status=404)

    subject = str(body.get('subject') or session_data.get('subject', 'General')).strip()
    topic = str(body.get('topic') or session_data.get('topic', 'General')).strip()
    focus = ''
    if isinstance(supplied_question, dict):
        focus = str(supplied_question.get('f', '')).strip()
    context = f"Subject: {subject}. Topic: {topic}."
    if focus:
        context += f" Focus area: {focus}."

    from asgiref.sync import async_to_sync
    from services.visualization_service import (
        VisualizationValidationError,
        get_visualization_service,
    )

    try:
        visualization = async_to_sync(get_visualization_service().plan_simulation)(
            question_text=question_text,
            context=context,
        )
    except VisualizationValidationError as exc:
        return JsonResponse({'error': str(exc)}, status=422)
    except Exception:
        logger.exception('Plan Mode visualization generation failed')
        return JsonResponse({'error': 'Sage could not generate this visual right now.'}, status=502)

    if not isinstance(visualization, dict) or not visualization.get('engine'):
        return JsonResponse({'error': 'Sage returned an incomplete visual. Please regenerate it.'}, status=502)

    if isinstance(supplied_question, dict):
        questions[question_index] = supplied_question
    questions[question_index]['visualization'] = visualization
    questions[question_index]['visualization_approved'] = False
    session_data['questions'] = questions
    request.session['plan_session'] = session_data
    request.session.modified = True

    return JsonResponse({
        'success': True,
        'visualization': visualization,
        'visualization_approved': False,
    })


@csrf_exempt
@login_required_custom(role='professor')
def api_plan_question_visual_approve(request):
    """Approve or withdraw approval from a generated draft-question visual."""
    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed'}, status=405)

    try:
        body = json.loads(request.body.decode('utf-8'))
        question_index = int(body.get('question_index'))
    except (TypeError, ValueError, json.JSONDecodeError):
        return JsonResponse({'error': 'Invalid question.'}, status=400)

    session_data = request.session.get('plan_session', {})
    questions = session_data.get('questions', [])
    if question_index < 0 or question_index >= len(questions):
        return JsonResponse({'error': 'That draft question is no longer available.'}, status=404)

    question = questions[question_index]
    approved = bool(body.get('approved', True))
    if approved and not isinstance(question.get('visualization'), dict):
        return JsonResponse({'error': 'Generate a visual before approving it.'}, status=400)

    question['visualization_approved'] = approved
    questions[question_index] = question
    session_data['questions'] = questions
    request.session['plan_session'] = session_data
    request.session.modified = True

    return JsonResponse({'success': True, 'visualization_approved': approved})


@csrf_exempt
@login_required_custom(role='professor')
def api_plan_question_visual_discard(request):
    """Remove a generated visual and its approval from a draft question."""
    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed'}, status=405)

    try:
        body = json.loads(request.body.decode('utf-8'))
        question_index = int(body.get('question_index'))
    except (TypeError, ValueError, json.JSONDecodeError):
        return JsonResponse({'error': 'Invalid question.'}, status=400)

    session_data = request.session.get('plan_session', {})
    questions = session_data.get('questions', [])
    if question_index < 0 or question_index >= len(questions):
        return JsonResponse({'error': 'That draft question is no longer available.'}, status=404)

    question = questions[question_index]
    question.pop('visualization', None)
    question['visualization_approved'] = False
    questions[question_index] = question
    session_data['questions'] = questions
    request.session['plan_session'] = session_data
    request.session.modified = True

    return JsonResponse({'success': True, 'visualization_approved': False})


@csrf_exempt
def api_plan_publish(request):
    """
    Publishes the finalized topic, notes, and question set to the database.
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed'}, status=405)

    try:
        body = json.loads(request.body.decode('utf-8'))
    except Exception:
        body = request.POST

    from services.plan_mode_service import publish_plan_to_database

    user = get_session_user(request)
    prof_profile = None
    if user:
        prof_profile = models.UserProfile.objects.filter(user__username=user['id']).first()
    if not prof_profile:
        prof_profile = models.UserProfile.objects.filter(role__role_code='professor').first()

    session_data = request.session.get('plan_session', {})
    subject = body.get('subject') or session_data.get('subject', 'Machine Learning')
    topic = body.get('topic') or session_data.get('topic', 'K-Means Clustering')
    course_name = str(body.get('course_name') or session_data.get('course_name') or topic).strip()
    if len(course_name) > 120:
        return JsonResponse({'error': 'Keep the course name under 120 characters.'}, status=400)
    batch = body.get('batch') or session_data.get('batch', 'Year 2')
    notes = body.get('notes') or session_data.get('notes', [])
    questions = body.get('questions') or session_data.get('questions', [])
    game_config = body.get('game_config')
    file_path = session_data.get('file_path', '')

    try:
        result = publish_plan_to_database(
            subject_name=subject,
            batch_name_or_code=batch,
            topic_name=topic,
            course_name=course_name,
            notes_list=notes,
            questions_list=questions,
            prof_profile=prof_profile,
            file_path=file_path,
            game_config=game_config,
        )
    except ValueError as exc:
        return JsonResponse({'error': str(exc)}, status=400)

    # Clear draft session
    if 'plan_session' in request.session:
        del request.session['plan_session']

    return JsonResponse(result)
