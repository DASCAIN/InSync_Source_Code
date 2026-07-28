from django.urls import path
from . import views

app_name = 'voice_tutor'

urlpatterns = [
    # Frontend HTML Pages
    path('', views.index_view, name='index'),
    path('login', views.login_view, name='login'),
    path('signup', views.signup_view, name='signup'),
    path('logout', views.logout_view, name='logout'),
    
    path('professor', views.professor_dashboard, name='professor_dashboard'),
    path('professor/batchs', views.professor_batchs, name='professor_batchs'),
    path('professor/batchs/<str:batch_id>', views.professor_batch_detail, name='professor_batch_detail'),
    path('professor/subjects', views.professor_subjects, name='professor_subjects'),
    path('professor/topics', views.professor_topics, name='professor_topics'),
    path('professor/topics/<int:topic_id>/update', views.professor_topic_update, name='professor_topic_update'),
    path('professor/topics/<int:topic_id>/delete', views.professor_topic_delete, name='professor_topic_delete'),
    path('professor/assignments', views.professor_assignments, name='professor_assignments'),
    path('professor/assignments/<str:assignment_id>', views.professor_assignment_detail, name='professor_assignment_detail'),
    path('professor/assignments/<str:assignment_id>/delete', views.professor_assignment_delete, name='professor_assignment_delete'),
    path('professor/assignments/<str:assignment_id>/pdf', views.serve_assignment_pdf, name='serve_assignment_pdf'),
    path('professor/analytics', views.professor_analytics, name='professor_analytics'),
    path('professor/messages', views.professor_messages, name='professor_messages'),
    path('professor/guided-assignment/<str:session_id>', views.guided_assignment_view, name='guided_assignment_view'),
    
    path('student', views.student_dashboard, name='student_dashboard'),
    path('student/notes', views.student_notes_directory, name='student_notes_directory'),
    path('student/notes/<int:note_id>', views.student_note_detail, name='student_note_detail'),
    path('student/assignments/<str:assignment_id>/take', views.take_assignment_view, name='take_assignment'),
    path('student/assignments/<str:assignment_id>', views.voice_agent_view, name='voice_agent_assignment'),
    path('student/voice/<str:assignment_id>/<str:question_id>', views.voice_agent_view, name='voice_agent_question'),
    path('student/tutor-chat', views.tutor_chat_view, name='tutor_chat'),
    path('student/tutor-voice', views.openai_voice_tutor_view, name='openai_voice_tutor'),
    
    path('subject/<str:subject_id>/topic/<str:topic_id>', views.topic_page_view, name='topic_page'),

    # Backend API Endpoints (called via AJAX from templates)
    path('api/voice/start', views.api_start_call, name='api_start_call'),
    path('api/voice/status/<str:conversation_id>', views.api_get_call_status, name='api_get_call_status'),
    path('api/voice/end', views.api_end_call, name='api_end_call'),
    path('api/voice/history', views.api_get_call_history, name='api_get_call_history'),
    path('api/voice/transfer-lead', views.api_transfer_call_lead, name='api_transfer_call_lead'),
    path('api/tutor/chat', views.api_tutor_chat, name='api_tutor_chat'),
    path('api/tutor/guided-assignment-chat/<str:session_id>', views.api_guided_assignment_chat, name='api_guided_assignment_chat'),
    path('api/openai-voice/session', views.api_create_openai_voice_session, name='api_create_openai_voice_session'),
    path('api/openai-voice/memory', views.api_store_openai_voice_memory, name='api_store_openai_voice_memory'),
    path('api/openai-voice/recording', views.api_store_openai_voice_recording, name='api_store_openai_voice_recording'),
    path('api/student/assignment/<str:assignment_id>/complete', views.api_complete_assignment, name='api_complete_assignment'),
    path('api/student/assignment/<str:assignment_id>/time', views.api_update_time_spent, name='api_update_time_spent'),
]

