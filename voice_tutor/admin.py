from django.contrib import admin
from .models import (
    UserRoleMaster, UserProfile, UniversityMaster, CourseMaster,
    SessionMaster, SubjectMaster, ModuleMaster, TopicMaster,
    RawMaterial, SummaryNotes, Assignment, AssignmentQuestion,
    QuestionOption, TutorialVideo, StudentTopicInteraction,
    StudentTopicAssessmentReview, Batch, ProfessorAllocation
)

# Custom base ModelAdmin to handle audit logs
class AuditAdmin(admin.ModelAdmin):
    readonly_fields = ('created_at', 'updated_at', 'created_by', 'updated_by')

    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        obj.updated_by = request.user
        super().save_model(request, obj, form, change)

    def save_formset(self, request, form, formset, change):
        instances = formset.save(commit=False)
        for obj in formset.deleted_objects:
            obj.delete()
        for instance in instances:
            # Check if this inline instance has audit fields
            if hasattr(instance, 'created_by') and hasattr(instance, 'updated_by'):
                if not instance.pk:
                    instance.created_by = request.user
                instance.updated_by = request.user
            instance.save()
        formset.save_m2m()

class UserRoleMasterAdmin(AuditAdmin):
    list_display = ('role_name', 'role_code', 'created_at', 'updated_at')
    search_fields = ('role_name', 'role_code')
    ordering = ('role_name',)

class UserProfileAdmin(AuditAdmin):
    list_display = ('user', 'name', 'email', 'role', 'is_approved', 'created_at')
    list_filter = ('role', 'is_approved', 'created_at', 'university')
    search_fields = ('name', 'email', 'user__username')
    raw_id_fields = ('user',)
    list_editable = ('is_approved',)
    actions = ['approve_users']

    @admin.action(description="Approve selected user profiles and activate their accounts")
    def approve_users(self, request, queryset):
        count = 0
        for profile in queryset:
            profile.is_approved = True
            profile.save()
            if profile.user:
                profile.user.is_active = True
                profile.user.save()
            count += 1
        self.message_user(request, f"Successfully approved and activated {count} user profiles.")

    def save_model(self, request, obj, form, change):
        if obj.is_approved and obj.user:
            obj.user.is_active = True
            obj.user.save()
        elif not obj.is_approved and obj.user:
            obj.user.is_active = False
            obj.user.save()
        super().save_model(request, obj, form, change)


class UniversityMasterAdmin(AuditAdmin):
    list_display = ('name', 'code', 'created_at', 'updated_at')
    search_fields = ('name', 'code')

class CourseMasterAdmin(AuditAdmin):
    list_display = ('name', 'code', 'university', 'created_at')
    list_filter = ('university',)
    search_fields = ('name', 'code')

class SessionMasterAdmin(AuditAdmin):
    list_display = ('name', 'start_year', 'end_year', 'created_at')
    search_fields = ('name',)

class SubjectMasterAdmin(AuditAdmin):
    list_display = ('name', 'code', 'course', 'session', 'semester_or_year')
    list_filter = ('course', 'session', 'semester_or_year')
    search_fields = ('name', 'code')

class ModuleMasterAdmin(AuditAdmin):
    list_display = ('name', 'subject', 'order')
    list_filter = ('subject',)
    search_fields = ('name',)
    ordering = ('subject', 'order')

class TopicMasterAdmin(AuditAdmin):
    list_display = ('name', 'code', 'module', 'subject', 'date_time')
    list_filter = ('subject', 'module', 'date_time')
    search_fields = ('name', 'code')

class RawMaterialAdmin(AuditAdmin):
    list_display = ('title', 'topic', 'material_type', 'file_url', 'created_at')
    list_filter = ('material_type', 'topic__subject')
    search_fields = ('title', 'topic__name')

class SummaryNotesAdmin(AuditAdmin):
    list_display = ('title', 'topic', 'created_at')
    list_filter = ('topic__subject',)
    search_fields = ('title', 'topic__name')

class QuestionOptionInline(admin.TabularInline):
    model = QuestionOption
    extra = 4
    readonly_fields = ('created_at', 'updated_at', 'created_by', 'updated_by')

class AssignmentQuestionInline(admin.TabularInline):
    model = AssignmentQuestion
    extra = 1
    readonly_fields = ('created_at', 'updated_at', 'created_by', 'updated_by')

class AssignmentQuestionAdmin(AuditAdmin):
    list_display = ('code', 'assignment', 'question_type', 'difficulty', 'order')
    list_filter = ('question_type', 'difficulty', 'assignment__topic__subject')
    search_fields = ('content', 'code')
    inlines = [QuestionOptionInline]

class AssignmentAdmin(AuditAdmin):
    list_display = ('title', 'code', 'topic', 'professor_allocation', 'difficulty', 'expected_duration', 'created_at')
    list_filter = (
        'professor_allocation__subject',
        'topic',
        'professor_allocation__professor',
        'professor_allocation__batch',
        'difficulty'
    )
    ordering = ('-created_at',)
    search_fields = ('title', 'code', 'topic__name')
    inlines = [AssignmentQuestionInline]

class TutorialVideoAdmin(AuditAdmin):
    list_display = ('title', 'topic', 'src', 'created_at')
    list_filter = ('topic__subject',)
    search_fields = ('title', 'topic__name')

class StudentTopicInteractionAdmin(AuditAdmin):
    list_display = ('student', 'topic', 'interaction_type', 'started_at', 'ended_at')
    list_filter = ('interaction_type', 'topic__subject', 'started_at')
    search_fields = ('student__name', 'topic__name', 'transcript')

class StudentTopicAssessmentReviewAdmin(AuditAdmin):
    list_display = ('student', 'topic', 'score', 'engagement_score', 'status', 'created_at')
    list_filter = ('status', 'topic__subject')
    search_fields = ('student__name', 'topic__name')

class BatchAdmin(AuditAdmin):
    list_display = ('name', 'code', 'university', 'session', 'course', 'created_at')
    list_filter = ('university', 'session', 'course')
    search_fields = ('name', 'code')
    filter_horizontal = ('students',)

# Registering models in admin portal
admin.site.register(UserRoleMaster, UserRoleMasterAdmin)
admin.site.register(UserProfile, UserProfileAdmin)
admin.site.register(UniversityMaster, UniversityMasterAdmin)
admin.site.register(CourseMaster, CourseMasterAdmin)
admin.site.register(SessionMaster, SessionMasterAdmin)
admin.site.register(SubjectMaster, SubjectMasterAdmin)
admin.site.register(ModuleMaster, ModuleMasterAdmin)
admin.site.register(TopicMaster, TopicMasterAdmin)
admin.site.register(RawMaterial, RawMaterialAdmin)
admin.site.register(SummaryNotes, SummaryNotesAdmin)
admin.site.register(Assignment, AssignmentAdmin)
admin.site.register(AssignmentQuestion, AssignmentQuestionAdmin)
admin.site.register(QuestionOption)
admin.site.register(TutorialVideo, TutorialVideoAdmin)
admin.site.register(StudentTopicInteraction, StudentTopicInteractionAdmin)
admin.site.register(StudentTopicAssessmentReview, StudentTopicAssessmentReviewAdmin)
admin.site.register(Batch, BatchAdmin)

@admin.register(ProfessorAllocation)
class ProfessorAllocationAdmin(admin.ModelAdmin):
    list_display = ('batch', 'subject', 'professor', 'created_at')
    list_filter = ('batch', 'subject', 'professor')
    search_fields = ('batch__name', 'subject__name', 'professor__name')
