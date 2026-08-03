import json
from django.test import TestCase, Client
from django.urls import reverse
from voice_tutor.models import VoiceCall

class InSyncViewsTestCase(TestCase):
    def setUp(self):
        self.client = Client()
        
    def test_landing_page(self):
        response = self.client.get(reverse('voice_tutor:index'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "InSync")
        self.assertContains(response, "by DASCAIN")

    def test_login_page_get(self):
        response = self.client.get(reverse('voice_tutor:login'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Sign In")

    def test_login_student_success(self):
        response = self.client.post(reverse('voice_tutor:login'), {
            'email': 'student@university.edu',
            'role': 'student'
        })
        # Should redirect to student dashboard
        self.assertRedirects(response, reverse('voice_tutor:student_dashboard'))
        # Session should contain student user info
        session = self.client.session
        self.assertIn('user', session)
        self.assertEqual(session['user']['role'], 'student')
        self.assertEqual(session['user']['email'], 'student@university.edu')

    def test_login_professor_success(self):
        response = self.client.post(reverse('voice_tutor:login'), {
            'email': 'professor@university.edu',
            'role': 'professor'
        })
        # Should redirect to professor dashboard
        self.assertRedirects(response, reverse('voice_tutor:professor_dashboard'))
        # Session should contain professor user info
        session = self.client.session
        self.assertIn('user', session)
        self.assertEqual(session['user']['role'], 'professor')
        self.assertEqual(session['user']['email'], 'professor@university.edu')

    def test_login_invalid_missing_email(self):
        response = self.client.post(reverse('voice_tutor:login'), {
            'email': '',
            'role': 'student'
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Invalid login parameters")

    def test_logout(self):
        # Log in first
        self.client.post(reverse('voice_tutor:login'), {
            'email': 'student@university.edu',
            'role': 'student'
        })
        self.assertIn('user', self.client.session)
        
        # Log out
        response = self.client.get(reverse('voice_tutor:logout'))
        self.assertRedirects(response, reverse('voice_tutor:index'))
        self.assertNotIn('user', self.client.session)

    def test_student_dashboard_requires_login(self):
        response = self.client.get(reverse('voice_tutor:student_dashboard'))
        self.assertRedirects(response, reverse('voice_tutor:login'))

    def test_student_dashboard_as_student(self):
        self.client.post(reverse('voice_tutor:login'), {
            'email': 'student@university.edu',
            'role': 'student'
        })
        response = self.client.get(reverse('voice_tutor:student_dashboard'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Rahul") # Student name
        self.assertContains(response, "Subjects")

    def test_student_dashboard_as_professor_redirects(self):
        self.client.post(reverse('voice_tutor:login'), {
            'email': 'professor@university.edu',
            'role': 'professor'
        })
        response = self.client.get(reverse('voice_tutor:student_dashboard'))
        # Should redirect professor to professor dashboard
        self.assertRedirects(response, reverse('voice_tutor:professor_dashboard'))

    def test_professor_dashboard_requires_login(self):
        response = self.client.get(reverse('voice_tutor:professor_dashboard'))
        self.assertRedirects(response, reverse('voice_tutor:login'))

    def test_professor_dashboard_as_professor(self):
        self.client.post(reverse('voice_tutor:login'), {
            'email': 'professor@university.edu',
            'role': 'professor'
        })
        response = self.client.get(reverse('voice_tutor:professor_dashboard'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Dashboard")
        self.assertContains(response, "Need Attention")

    def test_professor_dashboard_as_student_redirects(self):
        self.client.post(reverse('voice_tutor:login'), {
            'email': 'student@university.edu',
            'role': 'student'
        })
        response = self.client.get(reverse('voice_tutor:professor_dashboard'))
        # Should redirect student to student dashboard
        self.assertRedirects(response, reverse('voice_tutor:student_dashboard'))

    def test_student_tutor_chat(self):
        self.client.post(reverse('voice_tutor:login'), {
            'email': 'student@university.edu',
            'role': 'student'
        })
        response = self.client.get(reverse('voice_tutor:tutor_chat'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Chat with AI Tutor")

    def test_student_voice_agent(self):
        self.client.post(reverse('voice_tutor:login'), {
            'email': 'student@university.edu',
            'role': 'student'
        })
        response = self.client.get(reverse('voice_tutor:voice_agent_assignment', args=['A001']))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "ElevenLabs")

    def test_student_openai_voice_tutor(self):
        self.client.post(reverse('voice_tutor:login'), {
            'email': 'student@university.edu',
            'role': 'student'
        })
        response = self.client.get(reverse('voice_tutor:openai_voice_tutor'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "WebRTC")

    def test_professor_batchs_page(self):
        self.client.post(reverse('voice_tutor:login'), {
            'email': 'professor@university.edu',
            'role': 'professor'
        })
        response = self.client.get(reverse('voice_tutor:professor_batchs'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "JEE Probability Batch") # batch name from dummy data

    def test_professor_assignments_page(self):
        self.client.post(reverse('voice_tutor:login'), {
            'email': 'professor@university.edu',
            'role': 'professor'
        })
        response = self.client.get(reverse('voice_tutor:professor_assignments'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Probability Basics")

    def test_professor_analytics_page(self):
        self.client.post(reverse('voice_tutor:login'), {
            'email': 'professor@university.edu',
            'role': 'professor'
        })
        response = self.client.get(reverse('voice_tutor:professor_analytics'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Engagement")

    def test_professor_messages_page(self):
        self.client.post(reverse('voice_tutor:login'), {
            'email': 'professor@university.edu',
            'role': 'professor'
        })
        response = self.client.get(reverse('voice_tutor:professor_messages'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Select a student to start messaging")

    def test_api_get_call_history_empty(self):
        # We can fetch call history as JSON
        response = self.client.get(reverse('voice_tutor:api_get_call_history'))
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertIsInstance(data, list)

    def test_signup_page_get(self):
        response = self.client.get(reverse('voice_tutor:signup'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Create Account")
        self.assertContains(response, "Select Role")

    def test_signup_student_flow(self):
        from voice_tutor import models
        from django.contrib.auth.models import User
        # Ensure database has seeded masters to select
        self.client.get(reverse('voice_tutor:signup')) # Triggers auto-seed
        
        univ = models.UniversityMaster.objects.first()
        course = models.CourseMaster.objects.first()
        session = models.SessionMaster.objects.first()
        subj = models.SubjectMaster.objects.first()

        response = self.client.post(reverse('voice_tutor:signup'), {
            'role': 'student',
            'name': 'Test Student',
            'email': 'test_student@student.edu',
            'password': 'password123',
            'mobile_number': '1234567890',
            'dob': '2005-05-15',
            'university': univ.id,
            'course': course.id,
            'session': session.id,
            'enrollment_number': 'ENR2026999',
            'preferred_subjects': [subj.id]
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Registration Successful!")

        # Verify database objects
        user = User.objects.get(email='test_student@student.edu')
        self.assertFalse(user.is_active) # Must stay inactive

    def test_signup_student_optional_password_and_dob(self):
        from voice_tutor import models
        from django.contrib.auth.models import User
        self.client.get(reverse('voice_tutor:signup')) # Triggers auto-seed
        
        univ = models.UniversityMaster.objects.first()
        course = models.CourseMaster.objects.first()
        session = models.SessionMaster.objects.first()

        response = self.client.post(reverse('voice_tutor:signup'), {
            'role': 'student',
            'name': 'Arijit Srivastava',
            'email': 'arijit.autogen@nmims.in',
            'password': '',  # Left empty for auto-generation
            'mobile_number': '9833872217',
            'dob': '',  # Left empty (optional)
            'university': univ.id,
            'course': course.id,
            'session': session.id,
            'enrollment_number': '70092300023',
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Registration Successful!")

        # Verify auto-generated password: 98338 (first 5 of mobile) + 023 (last 3 of enrollment) = 98338023
        user = User.objects.get(email='arijit.autogen@nmims.in')
        self.assertTrue(user.check_password('98338023'))
        self.assertIsNone(user.profile.dob)

        profile = models.UserProfile.objects.get(user=user)
        self.assertEqual(profile.name, 'Test Student')
        self.assertEqual(profile.enrollment_number, 'ENR2026999')

    def test_change_password_flow(self):
        from django.contrib.auth.models import User
        # Create a user with initial password
        user = User.objects.create_user(username='change_test@university.edu', email='change_test@university.edu', password='old_password123')
        
        # Test change password POST
        response = self.client.post(reverse('voice_tutor:change_password'), {
            'username': 'change_test@university.edu',
            'current_password': 'old_password123',
            'new_password': 'new_password456',
            'confirm_password': 'new_password456'
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Password changed successfully!")

        # Verify password updated in database
        user.refresh_from_db()
        self.assertTrue(user.check_password('new_password456'))
        self.assertFalse(profile.is_approved)
        self.assertEqual(list(profile.preferred_subjects.all()), [subj])

    def test_signup_professor_flow(self):
        from voice_tutor import models
        from django.contrib.auth.models import User
        # Ensure database has seeded masters
        self.client.get(reverse('voice_tutor:signup'))
        
        univ = models.UniversityMaster.objects.first()
        subj = models.SubjectMaster.objects.first()

        response = self.client.post(reverse('voice_tutor:signup'), {
            'role': 'professor',
            'name': 'Test Professor',
            'email': 'test_prof@university.edu',
            'password': 'password123',
            'mobile_number': '9876543210',
            'dob': '1980-08-20',
            'university': univ.id,
            'emp_id': 'EMP99999',
            'preferred_subjects': [subj.id]
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Registration Successful!")

        # Verify database objects
        user = User.objects.get(email='test_prof@university.edu')
        self.assertFalse(user.is_active)

        profile = models.UserProfile.objects.get(user=user)
        self.assertEqual(profile.name, 'Test Professor')
        self.assertEqual(profile.emp_id, 'EMP99999')
        self.assertFalse(profile.is_approved)

    def test_login_unapproved_user_fails(self):
        from voice_tutor import models
        from django.contrib.auth.models import User
        # Seed masters
        self.client.get(reverse('voice_tutor:signup'))
        
        student_role = models.UserRoleMaster.objects.get(role_code='student')
        user = User.objects.create_user(username='inactive_student', email='inactive@student.edu', password='password')
        user.is_active = False
        user.save()

        models.UserProfile.objects.create(
            user=user,
            name='Inactive Student',
            email='inactive@student.edu',
            role=student_role,
            is_approved=False
        )

        response = self.client.post(reverse('voice_tutor:login'), {
            'email': 'inactive@student.edu',
            'role': 'student'
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "pending administrator approval")

    def test_login_approved_user_succeeds(self):
        from voice_tutor import models
        from django.contrib.auth.models import User
        # Seed masters
        self.client.get(reverse('voice_tutor:signup'))
        
        student_role = models.UserRoleMaster.objects.get(role_code='student')
        user = User.objects.create_user(username='active_student', email='active@student.edu', password='password')
        # Simulate Admin approval: sets active and approved
        user.is_active = True
        user.save()

        models.UserProfile.objects.create(
            user=user,
            name='Active Student',
            email='active@student.edu',
            role=student_role,
            is_approved=True
        )

        response = self.client.post(reverse('voice_tutor:login'), {
            'email': 'active@student.edu',
            'role': 'student'
        })
        self.assertRedirects(response, reverse('voice_tutor:student_dashboard'))


