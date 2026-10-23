from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('voice_tutor', '0013_chatsession_chatmessage'),
    ]

    operations = [
        migrations.AddField(
            model_name='assignmentquestion',
            name='visualization_approved',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='assignmentquestion',
            name='visualization_approved_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='assignmentquestion',
            name='visualization_approved_by',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='approved_question_visualizations',
                to='voice_tutor.userprofile',
            ),
        ),
        migrations.AddField(
            model_name='assignmentquestion',
            name='visualization_data',
            field=models.JSONField(blank=True, null=True),
        ),
    ]
