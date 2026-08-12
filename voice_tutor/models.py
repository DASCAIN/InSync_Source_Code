from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone

class VoiceCall(models.Model):
    conversation_id = models.CharField(max_length=255, primary_key=True)
    lead_id = models.CharField(max_length=255, null=True, blank=True)
    user_id = models.CharField(max_length=255, null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=50, null=True, blank=True)

    class Meta:
        db_table = 'voice_calls'
        managed = True  # Allows Django migrations to track it, but we fake the initial run

class AuditModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='%(class)s_created')
    updated_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='%(class)s_updated')

    class Meta:
        abstract = True

class UserRoleMaster(AuditModel):
    role_name = models.CharField(max_length=100)
    role_code = models.CharField(max_length=50, unique=True) # e.g. student, professor, admin

    def __str__(self):
        return self.role_name

    class Meta:
        db_table = 'user_role_master'

class UserProfile(AuditModel):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    name = models.CharField(max_length=255)
    email = models.EmailField(unique=True)
    role = models.ForeignKey(UserRoleMaster, on_delete=models.PROTECT, related_name='users')
    avatar = models.CharField(max_length=255, null=True, blank=True)
    
    # Sign up and role-specific fields
    mobile_number = models.CharField(max_length=20, null=True, blank=True)
    dob = models.DateField(null=True, blank=True)
    university = models.ForeignKey('UniversityMaster', on_delete=models.SET_NULL, null=True, blank=True, related_name='profiles')
    emp_id = models.CharField(max_length=50, null=True, blank=True)
    enrollment_number = models.CharField(max_length=100, null=True, blank=True)
    course = models.ForeignKey('CourseMaster', on_delete=models.SET_NULL, null=True, blank=True, related_name='profiles')
    session = models.ForeignKey('SessionMaster', on_delete=models.SET_NULL, null=True, blank=True, related_name='profiles')
    preferred_subjects = models.ManyToManyField('SubjectMaster', blank=True, related_name='preferred_by_users')
    is_approved = models.BooleanField(default=False)

    def __str__(self):
        return f"{self.name} ({self.role.role_name})"


    class Meta:
        db_table = 'user_profile'


class UniversityMaster(AuditModel):
    name = models.CharField(max_length=255)
    code = models.CharField(max_length=50, unique=True)
    address = models.TextField(null=True, blank=True)

    def __str__(self):
        return f"{self.name} ({self.code})"

    class Meta:
        db_table = 'university_master'

class CourseMaster(AuditModel):
    name = models.CharField(max_length=255)
    code = models.CharField(max_length=50, unique=True)
    university = models.ForeignKey(UniversityMaster, on_delete=models.CASCADE, related_name='courses')

    def __str__(self):
        return f"{self.name} ({self.code})"

    class Meta:
        db_table = 'course_master'

class SessionMaster(AuditModel):
    name = models.CharField(max_length=100) # e.g. "2024 to 2028"
    start_year = models.IntegerField()
    end_year = models.IntegerField()

    def __str__(self):
        return self.name

    class Meta:
        db_table = 'session_master'

class SubjectMaster(AuditModel):
    name = models.CharField(max_length=255)
    code = models.CharField(max_length=50, unique=True) # e.g. SUB001
    course = models.ForeignKey(CourseMaster, on_delete=models.CASCADE, related_name='subjects')
    session = models.ForeignKey(SessionMaster, on_delete=models.CASCADE, related_name='subjects')
    semester_or_year = models.CharField(max_length=100) # e.g. Semester 5
    description = models.TextField(null=True, blank=True)

    def __str__(self):
        return f"{self.name} ({self.code})"

    class Meta:
        db_table = 'subject_master'

class ModuleMaster(AuditModel):
    name = models.CharField(max_length=255)
    subject = models.ForeignKey(SubjectMaster, on_delete=models.CASCADE, related_name='modules')
    order = models.IntegerField(default=1)

    def __str__(self):
        return f"{self.subject.code} - {self.name}"

    class Meta:
        db_table = 'module_master'
        ordering = ['order']

class TopicMaster(AuditModel):
    name = models.CharField(max_length=255)
    code = models.CharField(max_length=50, unique=True) # e.g. TOP001
    module = models.ForeignKey(ModuleMaster, on_delete=models.CASCADE, related_name='topics', null=True, blank=True)
    subject = models.ForeignKey(SubjectMaster, on_delete=models.CASCADE, related_name='topics')
    description = models.TextField(null=True, blank=True)
    learning_objectives = models.TextField(null=True, blank=True) # JSON array of strings
    date_time = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return f"{self.code} - {self.name}"

    class Meta:
        db_table = 'topic_master'

class RawMaterial(AuditModel):
    MATERIAL_TYPES = [
        ('pdf', 'PDF'),
        ('image', 'Image'),
        ('doc', 'Document'),
        ('video', 'Video'),
        ('youtube', 'YouTube Link'),
    ]
    topic = models.ForeignKey(TopicMaster, on_delete=models.CASCADE, related_name='raw_materials')
    title = models.CharField(max_length=255)
    material_type = models.CharField(max_length=50, choices=MATERIAL_TYPES)
    file = models.FileField(upload_to='raw_materials/', null=True, blank=True)
    file_url = models.CharField(max_length=500, null=True, blank=True)

    def __str__(self):
        return f"{self.title} ({self.material_type})"

    class Meta:
        db_table = 'raw_material'

class SummaryNotes(AuditModel):
    topic = models.ForeignKey(TopicMaster, on_delete=models.CASCADE, related_name='summary_notes')
    title = models.CharField(max_length=255)
    content = models.TextField(null=True, blank=True)
    file = models.FileField(upload_to='summary_notes/', null=True, blank=True)
    file_url = models.CharField(max_length=500, null=True, blank=True)

    def __str__(self):
        return self.title

    class Meta:
        db_table = 'summary_notes'

class Assignment(AuditModel):
    topic = models.ForeignKey(TopicMaster, on_delete=models.CASCADE, related_name='assignments', null=True, blank=True)
    code = models.CharField(max_length=50, unique=True) # e.g. A001
    title = models.CharField(max_length=255)
    description = models.TextField(null=True, blank=True)
    difficulty = models.CharField(max_length=50, default='beginner') # beginner, intermediate, advanced
    expected_duration = models.IntegerField(default=30) # in minutes
    source_pdf_path = models.CharField(max_length=500, null=True, blank=True)
    professor_allocation = models.ForeignKey('ProfessorAllocation', on_delete=models.SET_NULL, null=True, blank=True, related_name='assignments')

    def __str__(self):
        return f"{self.code} - {self.title}"

    class Meta:
        db_table = 'assignment'

class AssignmentQuestion(AuditModel):
    QUESTION_TYPES = [
        ('mcq', 'Multiple Choice Question'),
        ('short_answer', 'Short Answer'),
        ('long_answer', 'Long Answer'),
        ('numerical', 'Numerical'),
        ('descriptive', 'Descriptive'),
    ]
    assignment = models.ForeignKey(Assignment, on_delete=models.CASCADE, related_name='questions')
    code = models.CharField(max_length=50) # e.g. BQ001, Q001
    content = models.TextField()
    question_type = models.CharField(max_length=50, choices=QUESTION_TYPES)
    correct_answer = models.TextField()
    concept_tags = models.TextField(null=True, blank=True) # JSON array of strings
    difficulty = models.CharField(max_length=50, default='beginner')
    order = models.IntegerField(default=1)

    def __str__(self):
        return f"{self.assignment.code} - {self.code}"

    class Meta:
        db_table = 'assignment_question'
        ordering = ['order']

class QuestionOption(AuditModel):
    question = models.ForeignKey(AssignmentQuestion, on_delete=models.CASCADE, related_name='options')
    option_letter = models.CharField(max_length=10) # e.g. a, b, c, d
    option_text = models.CharField(max_length=255)

    def __str__(self):
        return f"{self.question.code} - {self.option_letter}) {self.option_text}"

    class Meta:
        db_table = 'question_option'
        ordering = ['option_letter']

class TutorialVideo(AuditModel):
    topic = models.ForeignKey(TopicMaster, on_delete=models.CASCADE, related_name='tutorial_videos')
    code = models.CharField(max_length=50, null=True, blank=True) # e.g. VID001
    title = models.CharField(max_length=255)
    src = models.CharField(max_length=255) # e.g. /videos/boosting.mp4
    description = models.TextField(null=True, blank=True)

    def __str__(self):
        return self.title

    class Meta:
        db_table = 'tutorial_video'

class StudentTopicInteraction(AuditModel):
    student = models.ForeignKey(UserProfile, on_delete=models.CASCADE, related_name='interactions')
    topic = models.ForeignKey(TopicMaster, on_delete=models.CASCADE, related_name='interactions')
    interaction_type = models.CharField(max_length=50) # voice, chat
    transcript = models.TextField(null=True, blank=True) # JSON or text log
    recording_url = models.CharField(max_length=500, null=True, blank=True)
    started_at = models.DateTimeField(default=timezone.now)
    ended_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.student.name} - {self.topic.name} - {self.interaction_type}"

    class Meta:
        db_table = 'student_topic_interaction'

class StudentTopicAssessmentReview(AuditModel):
    student = models.ForeignKey(UserProfile, on_delete=models.CASCADE, related_name='assessments')
    topic = models.ForeignKey(TopicMaster, on_delete=models.CASCADE, related_name='assessments')
    assignment = models.ForeignKey(Assignment, on_delete=models.CASCADE, related_name='assessments', null=True, blank=True)
    score = models.IntegerField(null=True, blank=True)
    questions_completed = models.IntegerField(default=0)
    total_questions = models.IntegerField(default=0)
    time_spent = models.IntegerField(default=0) # in seconds
    hints_used = models.IntegerField(default=0)
    engagement_score = models.IntegerField(default=0)
    status = models.CharField(max_length=50, default='in_progress') # completed, in_progress, not_started
    review_comments = models.TextField(null=True, blank=True)
    saved_answers = models.JSONField(null=True, blank=True)

    def __str__(self):
        return f"{self.student.name} - {self.topic.name} - {self.status}"

    class Meta:
        db_table = 'student_topic_assessment_review'

class Batch(AuditModel):
    code = models.CharField(max_length=50, unique=True) # e.g. B101
    name = models.CharField(max_length=255)
    description = models.TextField(null=True, blank=True)
    university = models.ForeignKey(UniversityMaster, on_delete=models.CASCADE, related_name='batches', null=True, blank=True)
    session = models.ForeignKey(SessionMaster, on_delete=models.CASCADE, related_name='batches', null=True, blank=True)
    course = models.ForeignKey(CourseMaster, on_delete=models.CASCADE, related_name='batches', null=True, blank=True)
    students = models.ManyToManyField(UserProfile, related_name='student_batches', limit_choices_to={'role__role_code': 'student'})

    def __str__(self):
        return self.name

    class Meta:
        db_table = 'batch'
        verbose_name = 'Batch'
        verbose_name_plural = 'Batches'



class ProfessorAllocation(AuditModel):
    batch = models.ForeignKey(Batch, on_delete=models.CASCADE, related_name='allocations')
    subject = models.ForeignKey(SubjectMaster, on_delete=models.CASCADE, related_name='allocations')
    professor = models.ForeignKey(UserProfile, on_delete=models.CASCADE, related_name='allocated_batches', limit_choices_to={'role__role_code': 'professor'})

    class Meta:
        db_table = 'professor_allocation'
        unique_together = ('batch', 'subject', 'professor')
        verbose_name = 'Professor Allocation'
        verbose_name_plural = 'Professor Allocations'

    def __str__(self):
        return f"{self.batch.name} - {self.subject.name} - {self.professor.name}"
