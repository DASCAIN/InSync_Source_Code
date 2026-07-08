import json
import datetime
from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from django.utils import timezone
from voice_tutor.models import (
    UserRoleMaster, UserProfile, UniversityMaster, CourseMaster,
    SessionMaster, SubjectMaster, ModuleMaster, TopicMaster,
    RawMaterial, SummaryNotes, Assignment, AssignmentQuestion,
    QuestionOption, TutorialVideo, StudentTopicInteraction,
    StudentTopicAssessmentReview, Cohort
)

class Command(BaseCommand):
    help = 'Seeds the database with mock/dummy data'

    def handle(self, *args, **options):
        self.stdout.write('Clearing old data...')
        # Clear custom tables
        Cohort.objects.all().delete()
        StudentTopicAssessmentReview.objects.all().delete()
        StudentTopicInteraction.objects.all().delete()
        TutorialVideo.objects.all().delete()
        QuestionOption.objects.all().delete()
        AssignmentQuestion.objects.all().delete()
        Assignment.objects.all().delete()
        SummaryNotes.objects.all().delete()
        RawMaterial.objects.all().delete()
        TopicMaster.objects.all().delete()
        ModuleMaster.objects.all().delete()
        SubjectMaster.objects.all().delete()
        SessionMaster.objects.all().delete()
        CourseMaster.objects.all().delete()
        UniversityMaster.objects.all().delete()
        UserProfile.objects.all().delete()
        UserRoleMaster.objects.all().delete()

        # Keep superusers, but clean other seeded auth Users if they exist
        User.objects.exclude(is_superuser=True).delete()

        self.stdout.write('Creating roles...')
        student_role = UserRoleMaster.objects.create(role_name='Student', role_code='student')
        prof_role = UserRoleMaster.objects.create(role_name='Professor', role_code='professor')
        admin_role = UserRoleMaster.objects.create(role_name='Admin', role_code='admin')

        self.stdout.write('Creating default university, course, and session...')
        univ = UniversityMaster.objects.create(name='University of InSync', code='INS', address='InSync Campus')
        course = CourseMaster.objects.create(name='Computer Science & Engineering', code='CSE', university=univ)
        session = SessionMaster.objects.create(name='2024 to 2028', start_year=2024, end_year=2028)

        self.stdout.write('Creating users...')
        # Create users
        # Anita Sharma (Professor)
        prof_user = User.objects.create_user(username='prof_001', email='prof.sharma@university.edu', password='password123')
        prof_profile = UserProfile.objects.create(
            user=prof_user,
            name='Dr. Anita Sharma',
            email='prof.sharma@university.edu',
            role=prof_role,
            is_approved=True
        )

        # Student 1: Rahul Kumar
        s1_user = User.objects.create_user(username='student_001', email='rahul.kumar@student.edu', password='password123')
        s1_profile = UserProfile.objects.create(
            user=s1_user,
            name='Rahul Kumar',
            email='rahul.kumar@student.edu',
            role=student_role,
            is_approved=True
        )

        # Student 2: Priya Singh
        s2_user = User.objects.create_user(username='student_002', email='priya.singh@student.edu', password='password123')
        s2_profile = UserProfile.objects.create(
            user=s2_user,
            name='Priya Singh',
            email='priya.singh@student.edu',
            role=student_role,
            is_approved=True
        )

        # Student 3: Arjun Patel
        s3_user = User.objects.create_user(username='student_003', email='arjun.patel@student.edu', password='password123')
        s3_profile = UserProfile.objects.create(
            user=s3_user,
            name='Arjun Patel',
            email='arjun.patel@student.edu',
            role=student_role,
            is_approved=True
        )

        self.stdout.write('Creating subject, modules, and topics...')
        # Create Subject: Machine Learning
        subject = SubjectMaster.objects.create(
            name='Machine Learning',
            code='SUB001',
            course=course,
            session=session,
            semester_or_year='Semester 5',
            description='Explore the fundamentals and advanced concepts of Machine Learning, from ensemble methods to deep learning architectures.'
        )

        # Create Module: Ensemble Learning
        module = ModuleMaster.objects.create(
            name='Ensemble Learning',
            subject=subject,
            order=1
        )

        # Create Topic: Probability Concepts
        learning_objectives = [
            'Understand the concept of sample space and events',
            'Differentiate between dependent and independent events',
            'Explain conditional probability',
            'Apply Bayes Theorem to solve problems'
        ]
        topic = TopicMaster.objects.create(
            name='Probability Concepts',
            code='TOP001',
            module=module,
            subject=subject,
            description='Understand foundational probability concepts — Learn about sample spaces, events, conditional probability, and Bayes theorem.',
            learning_objectives=json.dumps(learning_objectives),
            date_time=timezone.make_aware(datetime.datetime(2024, 2, 1, 10, 0, 0))
        )

        self.stdout.write('Creating topic tutorial videos...')
        TutorialVideo.objects.create(
            topic=topic,
            code='VID001',
            title='Probability Introduction',
            src='/videos/intro.mp4',
            description='Learn the basics of probability, sample spaces, and events.'
        )
        TutorialVideo.objects.create(
            topic=topic,
            code='VID002',
            title='Conditional Probability',
            src='/videos/conditional.mp4',
            description='Discover how conditional probability works and how to apply Bayes theorem.'
        )

        self.stdout.write('Creating assignments, questions, and options...')
        # Assignment 1: Probability Basics (A001) - mapped to topic for guided session
        a1 = Assignment.objects.create(
            topic=topic,
            code='A001',
            title='Probability Basics',
            description='Introduction to fundamental probability concepts including sample space, events, and basic probability calculations.',
            difficulty='beginner',
            expected_duration=30
        )

        # Assignment 1 Questions (Q001 to Q004)
        q1 = AssignmentQuestion.objects.create(
            assignment=a1,
            code='Q001',
            content='A jar contains 5 red marbles, 3 blue marbles, and 2 green marbles. If one marble is drawn at random, what is the probability of selecting a red marble?',
            question_type='numerical',
            correct_answer='0.5',
            concept_tags=json.dumps(['experiment', 'sample_space', 'event', 'counting', 'formula']),
            difficulty='beginner',
            order=1
        )
        q2 = AssignmentQuestion.objects.create(
            assignment=a1,
            code='Q002',
            content='Two fair dice are rolled. What is the probability of getting a sum of eight?',
            question_type='numerical',
            correct_answer='5/36',
            concept_tags=json.dumps(['experiment', 'sample_space', 'event', 'counting', 'formula']),
            difficulty='beginner',
            order=2
        )
        q3 = AssignmentQuestion.objects.create(
            assignment=a1,
            code='Q003',
            content='In a survey of 100 students, 60 prefer online classes and 40 prefer offline classes. If a student is selected at random, what is the probability that they prefer online classes?',
            question_type='numerical',
            correct_answer='0.6',
            concept_tags=json.dumps(['experiment', 'sample_space', 'event', 'formula']),
            difficulty='beginner',
            order=3
        )
        q4 = AssignmentQuestion.objects.create(
            assignment=a1,
            code='Q004',
            content='The probability of a student passing an exam is 0.75. What is the probability that the student fails?',
            question_type='numerical',
            correct_answer='0.25',
            concept_tags=json.dumps(['event', 'formula', 'answer']),
            difficulty='beginner',
            order=4
        )

        # Assignment 2: Conditional Probability (A002)
        a2 = Assignment.objects.create(
            topic=topic,
            code='A002',
            title='Conditional Probability',
            description='Learn about conditional probability, Bayes theorem, and their applications.',
            difficulty='intermediate',
            expected_duration=45
        )

        # Assignment 3 (Practice Questions for topic)
        a_practice = Assignment.objects.create(
            topic=topic,
            code='A_PRACTICE_TOP001',
            title='Probability Practice',
            description='Practice questions for probability concepts.',
            difficulty='intermediate',
            expected_duration=40
        )

        # Practice Questions (BQ001 to BQ010)
        bq1 = AssignmentQuestion.objects.create(
            assignment=a_practice,
            code='BQ001',
            content='A dataset contains 1,000 training samples. In bagging, how are multiple training datasets created from this original dataset?',
            question_type='mcq',
            correct_answer='b',
            concept_tags=json.dumps(['Bootstrap sampling', 'Bagging basics']),
            difficulty='beginner',
            order=1
        )
        QuestionOption.objects.create(question=bq1, option_letter='a', option_text='By removing noisy samples')
        QuestionOption.objects.create(question=bq1, option_letter='b', option_text='By random sampling with replacement')
        QuestionOption.objects.create(question=bq1, option_letter='c', option_text='By splitting dataset into equal parts')
        QuestionOption.objects.create(question=bq1, option_letter='d', option_text='By normalizing feature values')

        bq2 = AssignmentQuestion.objects.create(
            assignment=a_practice,
            code='BQ002',
            content='If we create a bootstrap sample of size 1,000 from a dataset of 1,000 samples, what is most likely true?',
            question_type='mcq',
            correct_answer='b',
            concept_tags=json.dumps(['Sampling with replacement']),
            difficulty='beginner',
            order=2
        )
        QuestionOption.objects.create(question=bq2, option_letter='a', option_text='All original samples will appear exactly once')
        QuestionOption.objects.create(question=bq2, option_letter='b', option_text='Some samples will appear multiple times')
        QuestionOption.objects.create(question=bq2, option_letter='c', option_text='Dataset size becomes smaller')
        QuestionOption.objects.create(question=bq2, option_letter='d', option_text='All samples will be unique')

        AssignmentQuestion.objects.create(
            assignment=a_practice,
            code='BQ003',
            content='Explain why bagging helps reduce model variance.',
            question_type='descriptive',
            correct_answer='Bagging reduces variance by training multiple models on different bootstrap samples and averaging their predictions. Since each model sees a slightly different subset of data, their individual errors tend to cancel out when combined, leading to a more stable and less variable prediction than any single model.',
            concept_tags=json.dumps(['Variance reduction', 'Ensemble averaging']),
            difficulty='intermediate',
            order=3
        )

        bq4 = AssignmentQuestion.objects.create(
            assignment=a_practice,
            code='BQ004',
            content='Five decision trees in a bagging ensemble produce the following predictions: 12, 14, 10, 13, 11. What is the final bagging prediction?',
            question_type='numerical',
            correct_answer='12',
            concept_tags=json.dumps(['Averaging predictions', 'Regression bagging']),
            difficulty='beginner',
            order=4
        )

        bq5 = AssignmentQuestion.objects.create(
            assignment=a_practice,
            code='BQ005',
            content='Five classifiers in a bagging ensemble predict the following classes: A, B, A, A, B. What is the final predicted class?',
            question_type='mcq',
            correct_answer='a',
            concept_tags=json.dumps(['Majority voting', 'Classification bagging']),
            difficulty='beginner',
            order=5
        )
        QuestionOption.objects.create(question=bq5, option_letter='a', option_text='Class A')
        QuestionOption.objects.create(question=bq5, option_letter='b', option_text='Class B')
        QuestionOption.objects.create(question=bq5, option_letter='c', option_text='Cannot be determined')
        QuestionOption.objects.create(question=bq5, option_letter='d', option_text='Both A and B equally')

        AssignmentQuestion.objects.create(
            assignment=a_practice,
            code='BQ006',
            content='Explain what Out-of-Bag (OOB) samples are and how they are used in bagging.',
            question_type='descriptive',
            correct_answer='Out-of-Bag samples are the data points that were not selected in a particular bootstrap sample (approximately 37% of the original data). They serve as a natural validation set — each model can be evaluated on its OOB samples, providing an unbiased estimate of generalization error without needing a separate validation set.',
            concept_tags=json.dumps(['Out-of-Bag estimation', 'Model validation']),
            difficulty='intermediate',
            order=6
        )

        AssignmentQuestion.objects.create(
            assignment=a_practice,
            code='BQ007',
            content='Why is Random Forest considered an extension of bagging? Explain the additional mechanism it introduces.',
            question_type='descriptive',
            correct_answer='Random Forest extends bagging by not only creating bootstrap samples of data but also randomly selecting a subset of features at each split in the decision tree. This additional randomness (feature bagging) reduces correlation between trees, making the ensemble even more effective at reducing variance compared to standard bagging.',
            concept_tags=json.dumps(['Multiple trees', 'Bootstrap sampling', 'Feature randomness']),
            difficulty='intermediate',
            order=7
        )

        bq8 = AssignmentQuestion.objects.create(
            assignment=a_practice,
            code='BQ008',
            content='Bagging mainly helps reduce:',
            question_type='mcq',
            correct_answer='b',
            concept_tags=json.dumps(['Bias-variance tradeoff']),
            difficulty='beginner',
            order=8
        )
        QuestionOption.objects.create(question=bq8, option_letter='a', option_text='Bias')
        QuestionOption.objects.create(question=bq8, option_letter='b', option_text='Variance')
        QuestionOption.objects.create(question=bq8, option_letter='c', option_text='Both bias and variance equally')
        QuestionOption.objects.create(question=bq8, option_letter='d', option_text='It increases bias')

        AssignmentQuestion.objects.create(
            assignment=a_practice,
            code='BQ009',
            content='If individual decision trees in a bagging ensemble are overfitting the training data, explain how bagging still manages to improve generalization performance.',
            question_type='descriptive',
            correct_answer='Even though individual trees overfit (high variance, low bias), bagging averages their predictions. Since each tree overfits to a different bootstrap sample, their errors are largely uncorrelated. Averaging uncorrelated high-variance models significantly reduces overall variance while preserving the low bias, resulting in better generalization.',
            concept_tags=json.dumps(['Overfitting mitigation', 'Ensemble generalization']),
            difficulty='advanced',
            order=9
        )

        AssignmentQuestion.objects.create(
            assignment=a_practice,
            code='BQ010',
            content='If 100 bagged models are highly correlated with each other, what happens to the ensemble performance? Explain why model diversity matters.',
            question_type='descriptive',
            correct_answer='If bagged models are highly correlated, the variance reduction benefit diminishes significantly. The variance of the ensemble average is proportional to the average correlation between models. Highly correlated models make similar errors, so averaging them provides little benefit. This is why model diversity is critical — techniques like Random Forest add feature randomness specifically to reduce inter-model correlation.',
            concept_tags=json.dumps(['Model diversity importance', 'Correlation effect']),
            difficulty='advanced',
            order=10
        )

        self.stdout.write('Creating student progress (Assessments)...')
        # Seed progress for students
        # Rahul (student_001) A001
        StudentTopicAssessmentReview.objects.create(
            student=s1_profile,
            topic=topic,
            assignment=a1,
            score=85,
            questions_completed=3,
            total_questions=4,
            time_spent=1200,
            hints_used=2,
            engagement_score=85,
            status='in_progress'
        )

        # Priya (student_002) A001
        StudentTopicAssessmentReview.objects.create(
            student=s2_profile,
            topic=topic,
            assignment=a1,
            score=92,
            questions_completed=4,
            total_questions=4,
            time_spent=1500,
            hints_used=1,
            engagement_score=92,
            status='completed'
        )

        # Arjun (student_003) A001
        StudentTopicAssessmentReview.objects.create(
            student=s3_profile,
            topic=topic,
            assignment=a1,
            score=45,
            questions_completed=1,
            total_questions=4,
            time_spent=300,
            hints_used=4,
            engagement_score=45,
            status='in_progress'
        )

        self.stdout.write('Creating cohorts...')
        # Cohort C101
        cohort = Cohort.objects.create(
            code='C101',
            name='JEE Probability Batch – 2026',
            description='Intensive probability module for JEE aspirants',
            professor=prof_profile,
            subject=subject
        )
        cohort.students.add(s1_profile, s2_profile, s3_profile)
        cohort.assignments.add(a1, a2)

        self.stdout.write('Creating conversation logs (Interactions)...')
        StudentTopicInteraction.objects.create(
            student=s1_profile,
            topic=topic,
            interaction_type='chat',
            transcript='The total number of marbles is 10, right?',
            started_at=timezone.make_aware(datetime.datetime(2024, 2, 12, 10, 30, 0))
        )
        StudentTopicInteraction.objects.create(
            student=s1_profile,
            topic=topic,
            interaction_type='chat',
            transcript='How do I count favorable outcomes?',
            started_at=timezone.make_aware(datetime.datetime(2024, 2, 12, 10, 31, 0))
        )

        # Seed SummaryNotes & RawMaterial as well
        SummaryNotes.objects.create(
            topic=topic,
            title='Bagging & Boosting Summary',
            content='Bagging and Boosting are both ensemble methods. Bagging aims to reduce variance, while Boosting aims to reduce bias.'
        )
        RawMaterial.objects.create(
            topic=topic,
            title='Ensemble Methods Lecture PDF',
            material_type='pdf',
            file_url='/static/docs/ensemble_methods.pdf'
        )

        self.stdout.write(self.style.SUCCESS('Successfully seeded database!'))
