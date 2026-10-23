from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('voice_tutor', '0014_assignmentquestion_visualization_approval'),
    ]

    operations = [
        migrations.AddField(
            model_name='assignment',
            name='oral_defense_enabled',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='assignment',
            name='oral_defense_config',
            field=models.JSONField(blank=True, null=True),
        ),
    ]
