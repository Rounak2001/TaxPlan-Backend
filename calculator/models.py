from django.db import models
from django.conf import settings

class CalculatorSave(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='calculator_saves')
    calculator_type = models.CharField(max_length=50) # e.g., 'partnership'
    data = models.JSONField()
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('user', 'calculator_type')

    def __str__(self):
        return f"{self.user.username} - {self.calculator_type}"
