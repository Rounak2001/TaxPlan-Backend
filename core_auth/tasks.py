from django_tasks import task

@task()
def send_otp_task(phone_number, otp):
    """
    Simulates sending an OTP by printing to the console.
    """
    print(f"\n--- [TASK] Sending OTP {otp} to {phone_number} ---\n")
