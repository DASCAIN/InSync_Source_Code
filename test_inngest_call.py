import os
import django
import asyncio
import sys

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'insync_backend.settings')
django.setup()

from app.services.inngest_functions import rag_generate_assignment
import inngest

async def main():
    ctx = inngest.Context(event=inngest.Event(name="app/assignment.generate", data={}), run_id="123")
    step = inngest.Step(client=None, ctx=ctx, middleware=None, provider=None, req_sig=None, step_run_id=None)
    try:
        await rag_generate_assignment(ctx=ctx, step=step)
    except Exception as e:
        print("ERROR:", type(e), e)

asyncio.run(main())
